"""
imap_downloader.py
Descarga adjuntos XML y ZIP desde cuentas Gmail vía IMAP.
Compatible con Gmail usando App Passwords (contraseñas de aplicación).
"""

import imaplib
import email
import zipfile
import io
import calendar
from email.header import decode_header
from pathlib import Path
from typing import Callable, Optional

from xml_classifier import XMLClassifier
from folder_manager import FolderManager


# Meses en inglés para el protocolo IMAP (formato: DD-Mon-YYYY)
_IMAP_MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# Posibles nombres de la carpeta "Todos los correos" en Gmail
_ALL_MAIL_CANDIDATES = [
    '"[Gmail]/All Mail"',
    '"[Gmail]/Todos los correos"',
    '"[Gmail]/Tous les messages"',
    '"[Gmail]/Alle Nachrichten"',
    "INBOX",
]


class IMAPDownloader:
    """
    Gestiona la conexión IMAP a Gmail y la descarga de adjuntos XML/ZIP.
    """

    IMAP_HOST = "imap.gmail.com"
    IMAP_PORT = 993

    def __init__(
        self,
        email_address: str,
        password: str,
        log: Optional[Callable[[str], None]] = None,
    ):
        self.email_address = email_address
        self.password = password
        self.log = log or print
        self._mail: Optional[imaplib.IMAP4_SSL] = None

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------

    def connect(self):
        """Establece la conexión IMAP y autentica."""
        self._mail = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT)
        self._mail.login(self.email_address, self.password)
        self.log(f"   ✓ Conectado a {self.email_address}")

    def disconnect(self):
        """Cierra la sesión IMAP de forma segura."""
        if self._mail:
            try:
                self._mail.logout()
            except Exception:
                pass
            self._mail = None

    def test_connection(self) -> bool:
        """Intenta conectar y retorna True si tiene éxito."""
        try:
            self.connect()
            self.disconnect()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Descarga del mes
    # ------------------------------------------------------------------

    def download_month(
        self,
        year: int,
        month: int,
        folder_mgr: FolderManager,
        xml_classifier: XMLClassifier,
        client_name: str,
    ) -> dict:
        """
        Descarga todos los XML, PDF y ZIP del mes indicado.
        Omite archivos ya descargados previamente (filtro anti-duplicados).
        Retorna un dict con estadísticas: { 'total': int, 'skipped': int, 'by_type': {str: int}, 'errors': int }
        """
        stats = {"total": 0, "skipped": 0, "by_type": {}, "errors": 0}

        # Índice de archivos ya descargados para evitar duplicados
        existing_files = folder_mgr.get_existing_filenames(client_name, year, month)
        if existing_files:
            self.log(f"   Archivos ya descargados (se omitirán): {len(existing_files)}")

        since_date = f"01-{_IMAP_MONTHS[month - 1]}-{year}"
        # Fecha de inicio del mes siguiente
        next_month = month % 12 + 1
        next_year = year + (1 if month == 12 else 0)
        before_date = f"01-{_IMAP_MONTHS[next_month - 1]}-{next_year}"

        # Buscar en la carpeta más amplia disponible
        folder = self._find_all_mail_folder()
        try:
            status, _ = self._mail.select(folder, readonly=True)
            if status != "OK":
                raise RuntimeError(f"No se pudo abrir {folder}")
        except Exception as e:
            self.log(f"   ⚠ Usando INBOX como alternativa: {e}")
            self._mail.select("INBOX", readonly=True)

        # Buscar correos en el rango de fechas
        search_criteria = f"SINCE {since_date} BEFORE {before_date}"
        _, msg_ids_raw = self._mail.search(None, search_criteria)
        msg_ids = msg_ids_raw[0].split() if msg_ids_raw[0] else []

        self.log(f"   Correos encontrados en el período: {len(msg_ids)}")

        seen_message_ids: set = set()

        for msg_id in msg_ids:
            try:
                _, msg_data = self._mail.fetch(msg_id, "(RFC822)")
                if not msg_data or msg_data[0] is None:
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                # Evitar procesar el mismo correo dos veces
                mid = msg.get("Message-ID", "")
                if mid and mid in seen_message_ids:
                    continue
                if mid:
                    seen_message_ids.add(mid)

                # Procesar adjuntos
                count, skipped, type_counts, errors = self._process_attachments(
                    msg, folder_mgr, xml_classifier, client_name, year, month,
                    existing_files
                )
                stats["total"] += count
                stats["skipped"] += skipped
                stats["errors"] += errors
                for k, v in type_counts.items():
                    stats["by_type"][k] = stats["by_type"].get(k, 0) + v

            except Exception as e:
                stats["errors"] += 1
                self.log(f"   ❌ Error en correo {msg_id}: {e}")

        return stats

    # ------------------------------------------------------------------
    # Procesamiento de adjuntos
    # ------------------------------------------------------------------

    def _process_attachments(
        self,
        msg,
        folder_mgr: FolderManager,
        xml_classifier: XMLClassifier,
        client_name: str,
        year: int,
        month: int,
        existing_files: set,
    ) -> tuple:
        """Extrae y guarda los adjuntos XML, PDF y ZIP de un mensaje."""
        count = 0
        skipped = 0
        type_counts: dict = {}
        errors = 0

        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if not part.get("Content-Disposition"):
                continue

            filename = self._decode_filename(part.get_filename() or "")
            if not filename:
                continue

            filename_lower = filename.lower()

            # Filtro anti-duplicados: omitir si ya existe en la carpeta del mes
            if filename_lower in existing_files:
                skipped += 1
                continue

            try:
                if filename_lower.endswith(".xml"):
                    data = part.get_payload(decode=True)
                    if data:
                        saved, doc_type = self._save_xml(
                            data, filename, folder_mgr, xml_classifier,
                            client_name, year, month
                        )
                        if saved:
                            count += 1
                            existing_files.add(filename_lower)
                            type_counts[doc_type] = type_counts.get(doc_type, 0) + 1

                elif filename_lower.endswith(".pdf"):
                    data = part.get_payload(decode=True)
                    if data:
                        saved = self._save_pdf(
                            data, filename, folder_mgr, client_name, year, month
                        )
                        if saved:
                            count += 1
                            existing_files.add(filename_lower)
                            type_counts["PDFs"] = type_counts.get("PDFs", 0) + 1

                elif filename_lower.endswith(".zip"):
                    data = part.get_payload(decode=True)
                    if data:
                        c, sk, tc = self._extract_zip(
                            data, folder_mgr, xml_classifier, client_name, year, month,
                            existing_files
                        )
                        count += c
                        skipped += sk
                        for k, v in tc.items():
                            type_counts[k] = type_counts.get(k, 0) + v

            except Exception as e:
                errors += 1
                self.log(f"     ⚠ Error con adjunto '{filename}': {e}")

        return count, skipped, type_counts, errors

    def _save_xml(
        self,
        data: bytes,
        filename: str,
        folder_mgr: FolderManager,
        xml_classifier: XMLClassifier,
        client_name: str,
        year: int,
        month: int,
    ) -> tuple:
        """Clasifica y guarda un XML. Retorna (éxito, nombre_tipo)."""
        doc_type = xml_classifier.classify(data)
        dest_dir = folder_mgr.ensure_subfolder(client_name, year, month, doc_type)
        filepath = folder_mgr.unique_filepath(dest_dir, filename)
        filepath.write_bytes(data)
        return True, doc_type

    def _save_pdf(
        self,
        data: bytes,
        filename: str,
        folder_mgr: FolderManager,
        client_name: str,
        year: int,
        month: int,
    ) -> bool:
        """Guarda un PDF en la subcarpeta 'PDFs' del mes. Retorna True si se guardó."""
        dest_dir = folder_mgr.ensure_subfolder(client_name, year, month, "PDFs")
        filepath = dest_dir / filename
        if filepath.exists():
            return False
        filepath.write_bytes(data)
        return True

    def _extract_zip(
        self,
        data: bytes,
        folder_mgr: FolderManager,
        xml_classifier: XMLClassifier,
        client_name: str,
        year: int,
        month: int,
        existing_files: set,
    ) -> tuple:
        """Extrae XMLs y PDFs de un ZIP. Retorna (count, skipped, type_counts)."""
        count = 0
        skipped = 0
        type_counts: dict = {}
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    fname_lower = info.filename.lower()
                    fname = Path(info.filename).name

                    if fname.lower() in existing_files:
                        skipped += 1
                        continue

                    if fname_lower.endswith(".xml"):
                        xml_data = zf.read(info.filename)
                        saved, doc_type = self._save_xml(
                            xml_data, fname, folder_mgr, xml_classifier,
                            client_name, year, month
                        )
                        if saved:
                            count += 1
                            existing_files.add(fname.lower())
                            type_counts[doc_type] = type_counts.get(doc_type, 0) + 1

                    elif fname_lower.endswith(".pdf"):
                        pdf_data = zf.read(info.filename)
                        saved = self._save_pdf(
                            pdf_data, fname, folder_mgr, client_name, year, month
                        )
                        if saved:
                            count += 1
                            existing_files.add(fname.lower())
                            type_counts["PDFs"] = type_counts.get("PDFs", 0) + 1

        except zipfile.BadZipFile:
            self.log("     ⚠ Archivo ZIP corrupto o inválido, omitiendo.")
        return count, skipped, type_counts

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def _find_all_mail_folder(self) -> str:
        """Detecta el nombre correcto de la carpeta 'Todos los correos' de Gmail."""
        try:
            _, folders = self._mail.list()
            for folder_info in folders:
                if folder_info and b"\\All" in folder_info:
                    # Extraer el nombre de la carpeta
                    parts = folder_info.decode("utf-8").split('"')
                    if len(parts) >= 2:
                        return f'"{parts[-2]}"'
        except Exception:
            pass
        return "INBOX"

    @staticmethod
    def _decode_filename(raw: str) -> str:
        """Decodifica el nombre del archivo del encabezado del correo."""
        if not raw:
            return ""
        try:
            decoded_parts = decode_header(raw)
            result = ""
            for part, enc in decoded_parts:
                if isinstance(part, bytes):
                    result += part.decode(enc or "utf-8", errors="ignore")
                else:
                    result += part
            return result.strip()
        except Exception:
            return raw
