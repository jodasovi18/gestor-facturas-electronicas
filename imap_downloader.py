"""
imap_downloader.py
Descarga adjuntos XML y PDF desde cuentas de correo vía IMAP.
Compatible con Gmail (App Passwords) y Outlook/Hotmail/Live.
"""

import imaplib
import email
import zipfile
import io
import socket
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

# Configuración IMAP por proveedor
# clave: sufijos de dominio que identifican al proveedor
_IMAP_PROVIDERS = {
    "gmail": {
        "domains":  ["gmail.com", "googlemail.com"],
        "host":     "imap.gmail.com",
        "port":     993,
        "requires_app_password": True,
    },
    "outlook": {
        "domains":  [
            "outlook.com", "hotmail.com", "hotmail.es", "hotmail.co",
            "live.com", "live.com.ar", "msn.com", "microsoft.com",
        ],
        "host":     "outlook.office365.com",
        "port":     993,
        "requires_app_password": False,
    },
}

_DEFAULT_PROVIDER = {
    "host": "imap.gmail.com",
    "port": 993,
    "requires_app_password": True,
}


def get_imap_config(email_address: str) -> dict:
    """
    Retorna la configuración IMAP correcta según el dominio del correo.
    Incluye host, port y si requiere contraseña de aplicación.
    """
    domain = email_address.split("@")[-1].lower().strip() if "@" in email_address else ""
    for provider in _IMAP_PROVIDERS.values():
        if any(domain == d or domain.endswith("." + d)
               for d in provider["domains"]):
            return provider
    return _DEFAULT_PROVIDER


class IMAPDownloader:
    """
    Gestiona la conexión IMAP y la descarga de adjuntos XML/PDF/ZIP.
    Soporta Gmail (contraseña de aplicación) y Outlook/Hotmail/Live
    (contraseña normal de la cuenta).
    """

    IMAP_TIMEOUT = 30   # segundos

    def __init__(
        self,
        email_address: str,
        password: str,
        log: Optional[Callable[[str], None]] = None,
    ):
        self.email_address = email_address
        self.password      = password
        self.log           = log or print
        self._mail: Optional[imaplib.IMAP4_SSL] = None

        # Detectar proveedor automáticamente
        self._provider = get_imap_config(email_address)
        self.IMAP_HOST = self._provider["host"]
        self.IMAP_PORT = self._provider["port"]

    # ------------------------------------------------------------------
    # Conexión
    # ------------------------------------------------------------------

    def connect(self):
        """Establece la conexión IMAP y autentica."""
        socket.setdefaulttimeout(self.IMAP_TIMEOUT)
        self._mail = imaplib.IMAP4_SSL(self.IMAP_HOST, self.IMAP_PORT)
        self._mail.login(self.email_address, self.password)
        proveedor = "Outlook" if "outlook" in self.IMAP_HOST else "Gmail"
        self.log(f"   ✓ Conectado a {self.email_address} ({proveedor})")

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
        """
        Extrae y guarda los adjuntos XML, PDF y ZIP de un mensaje.

        Cubre tres casos adicionales sobre la implementación básica:
          1. Adjuntos inline (sin Content-Disposition o con disposition=inline)
             — algunos proveedores omiten el header o usan inline en lugar de attachment.
          2. Correos reenviados (message/rfc822) — desciende al mensaje embebido
             y procesa sus adjuntos recursivamente.
          3. ZIP dentro de ZIP — _extract_zip recursa un nivel.
        """
        count = 0
        skipped = 0
        type_counts: dict = {}
        errors = 0

        for part in msg.walk():
            content_type = part.get_content_type()

            # Caso: correo reenviado — el adjunto es un mensaje completo embebido
            if content_type == "message/rfc822":
                inner_msgs = part.get_payload()
                if not isinstance(inner_msgs, list):
                    inner_msgs = [inner_msgs]
                for inner in inner_msgs:
                    try:
                        c, sk, tc, err = self._process_attachments(
                            inner, folder_mgr, xml_classifier,
                            client_name, year, month, existing_files
                        )
                        count += c; skipped += sk; errors += err
                        for k, v in tc.items():
                            type_counts[k] = type_counts.get(k, 0) + v
                    except Exception as e:
                        errors += 1
                        self.log(f"     ⚠ Error en mensaje embebido: {e}")
                continue

            if part.get_content_maintype() == "multipart":
                continue

            # Determinar nombre de archivo — admite tanto attachment como inline
            disposition = part.get("Content-Disposition", "")
            filename = self._decode_filename(part.get_filename() or "")

            # Si no hay nombre en Content-Disposition, intentar desde Content-Type
            if not filename:
                filename = self._decode_filename(
                    part.get_param("name", header="Content-Type") or ""
                )

            # Si sigue sin nombre, omitir (no es un adjunto de interés)
            if not filename:
                continue

            # Sanitizar nombre antes de cualquier comparación o guardado
            filename_original = filename
            filename = self._sanitize_filename(filename)
            if filename != filename_original:
                self.log(f"     ℹ Nombre sanitizado: '{filename_original}' → '{filename}'")

            filename_lower = filename.lower()

            # Filtro anti-duplicados
            if filename_lower in existing_files:
                skipped += 1
                continue

            try:
                if filename_lower.endswith(".xml"):
                    data = part.get_payload(decode=True)
                    if data:
                        saved, doc_type, *_ = self._save_xml(
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
        """
        Clasifica y guarda un XML.

        Usa la fecha de emisión del comprobante (FechaEmision) para determinar
        el mes/año de destino. Si el XML no contiene fecha legible, se usa el
        mes/año del correo como fallback (comportamiento anterior).

        Retorna (éxito, nombre_tipo, año_real, mes_real).
        """
        doc_type = xml_classifier.classify(data)

        # Intentar leer la fecha real del comprobante
        doc_date = xml_classifier.extract_date(data)
        if doc_date and (doc_date.year, doc_date.month) != (year, month):
            dest_year  = doc_date.year
            dest_month = doc_date.month
            self.log(
                f"     ℹ Comprobante con fecha {doc_date.strftime('%Y-%m-%d')} "
                f"→ guardado en {dest_year}/{folder_mgr.month_label(dest_month)} "
                f"(correo era de {year}/{folder_mgr.month_label(month)})"
            )
        else:
            dest_year  = year
            dest_month = month

        dest_dir = folder_mgr.ensure_subfolder(client_name, dest_year, dest_month, doc_type)
        filepath = folder_mgr.unique_filepath(dest_dir, filename)
        filepath.write_bytes(data)
        return True, doc_type, dest_year, dest_month

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
        _depth: int = 0,
    ) -> tuple:
        """
        Extrae XMLs y PDFs de un ZIP. Retorna (count, skipped, type_counts).
        Soporta un nivel de recursión para ZIPs dentro de ZIPs (_depth máximo: 1).
        """
        MAX_ZIP_DEPTH = 1
        count = 0
        skipped = 0
        type_counts: dict = {}
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    fname_lower = info.filename.lower()
                    fname = Path(info.filename).name
                    fname = self._sanitize_filename(fname)

                    if fname.lower() in existing_files:
                        skipped += 1
                        continue

                    if fname_lower.endswith(".xml"):
                        xml_data = zf.read(info.filename)
                        saved, doc_type, *_ = self._save_xml(
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

                    elif fname_lower.endswith(".zip") and _depth < MAX_ZIP_DEPTH:
                        self.log(f"     ℹ ZIP anidado encontrado: '{fname}' — extrayendo")
                        inner_data = zf.read(info.filename)
                        c, sk, tc = self._extract_zip(
                            inner_data, folder_mgr, xml_classifier,
                            client_name, year, month, existing_files,
                            _depth=_depth + 1,
                        )
                        count += c; skipped += sk
                        for k, v in tc.items():
                            type_counts[k] = type_counts.get(k, 0) + v

        except zipfile.BadZipFile:
            self.log("     ⚠ Archivo ZIP corrupto o inválido, omitiendo.")
        return count, skipped, type_counts

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """
        Elimina o reemplaza caracteres inválidos en nombres de archivo de Windows.
        Caracteres prohibidos: \\ / : * ? " < > |
        También elimina caracteres de control (saltos de línea, tabs, etc.),
        limpia espacios/puntos al final, y nombres reservados del sistema
        (CON, PRN, AUX, NUL, COM1-9, LPT1-9).
        Si el nombre queda vacío tras la limpieza, devuelve '_sin_nombre'.
        """
        import re as _re

        # Eliminar caracteres de control (incluye \n, \r, \t y otros)
        sanitized = _re.sub(r'[\x00-\x1f\x7f]', '', filename)

        # Reemplazar caracteres inválidos de Windows por guion bajo
        sanitized = _re.sub(r'[\\/:*?"<>|]', "_", sanitized)

        # Colapsar espacios múltiples que pudieran quedar tras eliminar \n
        sanitized = _re.sub(r'  +', ' ', sanitized).strip()

        # Quitar espacios y puntos al final del nombre (antes de la extensión)
        stem, _, ext = sanitized.rpartition(".")
        if stem:
            stem = stem.rstrip(" .")
            sanitized = f"{stem}.{ext}" if ext else stem
        else:
            sanitized = sanitized.rstrip(" .")

        # Nombres reservados de Windows (case-insensitive)
        _RESERVED = {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        name_upper = sanitized.upper().rpartition(".")[0] or sanitized.upper()
        if name_upper in _RESERVED:
            sanitized = f"_{sanitized}"

        return sanitized or "_sin_nombre"

    def _find_all_mail_folder(self) -> str:
        """
        Detecta la carpeta correcta para buscar todos los correos.
        - Gmail: busca la carpeta con atributo \\All (Todos los correos)
        - Outlook y otros: usa INBOX directamente
        """
        # Outlook no tiene carpeta "All Mail" — INBOX contiene todo
        if "outlook" in self.IMAP_HOST or "office365" in self.IMAP_HOST:
            return "INBOX"

        # Gmail: buscar carpeta con atributo \All
        try:
            _, folders = self._mail.list()
            for folder_info in folders:
                if folder_info and b"\\All" in folder_info:
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
