"""
auditor.py
Auditoría de integridad: compara los adjuntos disponibles en Gmail
contra los archivos descargados en disco para un cliente y período dado.

Uso desde la app: instanciar AuditResult y pasarlo a la UI.
Uso standalone:   python auditor.py  (requiere config_manager configurado)
"""

from __future__ import annotations

import imaplib
import email
import zipfile
import io
from dataclasses import dataclass, field
from email.header import decode_header
from pathlib import Path
from typing import Callable, Optional

from imap_downloader import IMAPDownloader, _IMAP_MONTHS

# Extensiones que el sistema descarga y por tanto audita
_AUDITED_EXTENSIONS = {".xml", ".pdf"}


@dataclass
class AuditEntry:
    """Representa un adjunto encontrado en Gmail."""
    filename_original: str      # Nombre tal como viene del correo
    filename_sanitized: str     # Nombre tras sanitización (el que se guarda en disco)
    extension: str              # .xml / .pdf
    source: str                 # "email" | "zip:<nombre_zip>"
    message_id: str             # Message-ID del correo de origen
    message_date: str           # Fecha del correo (header Date)
    found_on_disk: bool = False # ¿Existe en alguna subcarpeta del mes?
    disk_path: str = ""         # Ruta completa si se encontró


@dataclass
class AuditReport:
    """Resultado completo de la auditoría para un cliente y período."""
    client_name: str
    email_address: str
    year: int
    month: int

    total_in_gmail: int = 0
    total_on_disk: int = 0
    missing_count: int = 0
    ok_count: int = 0

    entries: list[AuditEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def missing(self) -> list[AuditEntry]:
        return [e for e in self.entries if not e.found_on_disk]

    @property
    def found(self) -> list[AuditEntry]:
        return [e for e in self.entries if e.found_on_disk]

    def summary_lines(self) -> list[str]:
        """Devuelve líneas de resumen legibles para mostrar en la UI o log."""
        lines = [
            f"Cliente:      {self.client_name}",
            f"Correo:       {self.email_address}",
            f"Período:      {self.month:02d}/{self.year}",
            f"En Gmail:     {self.total_in_gmail} adjuntos auditables",
            f"En disco:     {self.ok_count} encontrados  |  {self.missing_count} faltantes",
        ]
        if self.errors:
            lines.append(f"Errores:      {len(self.errors)}")
        return lines

    def to_text(self) -> str:
        """Genera el texto completo del reporte para exportar."""
        sep = "=" * 60
        lines = [sep, "  REPORTE DE AUDITORÍA — Gestor de Facturas Electrónicas", sep, ""]
        lines += self.summary_lines()
        lines.append("")

        if self.missing:
            lines.append(f"{'─' * 60}")
            lines.append(f"  FALTANTES EN DISCO ({self.missing_count})")
            lines.append(f"{'─' * 60}")
            for e in self.missing:
                lines.append(f"  ✗ {e.filename_sanitized}")
                lines.append(f"      Origen:  {e.source}")
                lines.append(f"      Correo:  {e.message_date}  [{e.message_id[:40]}...]")
        else:
            lines.append("  ✅ Todos los adjuntos de Gmail están en disco.")

        if self.errors:
            lines.append("")
            lines.append(f"{'─' * 60}")
            lines.append(f"  ERRORES DURANTE LA AUDITORÍA ({len(self.errors)})")
            lines.append(f"{'─' * 60}")
            for err in self.errors:
                lines.append(f"  ⚠ {err}")

        lines.append("")
        lines.append(sep)
        return "\n".join(lines)


class Auditor:
    """
    Conecta a Gmail, enumera todos los adjuntos auditables del período,
    y los compara contra los archivos en disco.
    """

    def __init__(
        self,
        email_address: str,
        password: str,
        log: Optional[Callable[[str], None]] = None,
    ):
        self._dl = IMAPDownloader(email_address, password, log=log or print)
        self.log = log or print

    def run(
        self,
        client_name: str,
        year: int,
        month: int,
        base_path: str,
    ) -> AuditReport:
        """
        Ejecuta la auditoría completa y retorna un AuditReport.

        1. Conecta a Gmail y lista todos los adjuntos XML/PDF del período
           (incluyendo los que están dentro de ZIPs).
        2. Levanta el índice de archivos en disco para ese mes.
        3. Cruza ambos conjuntos y marca cuáles faltan.
        """
        report = AuditReport(
            client_name=client_name,
            email_address=self._dl.email_address,
            year=year,
            month=month,
        )

        try:
            self._dl.connect()
            self.log(f"   ✓ Conectado a {self._dl.email_address}")

            # --- Paso 1: inventario desde Gmail ---
            self.log("   Levantando inventario desde Gmail...")
            entries = self._list_gmail_attachments(year, month, report)
            report.entries = entries
            report.total_in_gmail = len(entries)
            self.log(f"   Adjuntos auditables encontrados en Gmail: {len(entries)}")

            # --- Paso 2: índice de archivos en disco ---
            self.log("   Levantando índice de archivos en disco...")
            disk_index = self._build_disk_index(client_name, year, month, base_path)
            report.total_on_disk = len(disk_index)
            self.log(f"   Archivos en disco para el período: {len(disk_index)}")

            # --- Paso 3: cruce ---
            self.log("   Comparando...")
            for entry in entries:
                key = entry.filename_sanitized.lower()
                if key in disk_index:
                    entry.found_on_disk = True
                    entry.disk_path = str(disk_index[key])
                    report.ok_count += 1
                else:
                    report.missing_count += 1

        except Exception as exc:
            report.errors.append(f"Error general: {exc}")
            self.log(f"   ❌ Error durante auditoría: {exc}")
        finally:
            self._dl.disconnect()

        return report

    # ------------------------------------------------------------------
    # Inventario Gmail
    # ------------------------------------------------------------------

    def _list_gmail_attachments(
        self, year: int, month: int, report: AuditReport
    ) -> list[AuditEntry]:
        """Recorre los correos del período y devuelve una lista de AuditEntry."""
        entries: list[AuditEntry] = []
        seen_message_ids: set = set()

        mail = self._dl._mail
        since_date  = f"01-{_IMAP_MONTHS[month - 1]}-{year}"
        next_month  = month % 12 + 1
        next_year   = year + (1 if month == 12 else 0)
        before_date = f"01-{_IMAP_MONTHS[next_month - 1]}-{next_year}"

        folder = self._dl._find_all_mail_folder()
        try:
            status, _ = mail.select(folder, readonly=True)
            if status != "OK":
                raise RuntimeError(f"No se pudo abrir {folder}")
        except Exception as exc:
            self.log(f"   ⚠ Usando INBOX: {exc}")
            mail.select("INBOX", readonly=True)

        _, msg_ids_raw = mail.search(None, f"SINCE {since_date} BEFORE {before_date}")
        msg_ids = msg_ids_raw[0].split() if msg_ids_raw[0] else []
        self.log(f"   Correos en el período: {len(msg_ids)}")

        for msg_id in msg_ids:
            try:
                _, msg_data = mail.fetch(msg_id, "(RFC822)")
                if not msg_data or msg_data[0] is None:
                    continue

                msg = email.message_from_bytes(msg_data[0][1])
                mid  = msg.get("Message-ID", f"<unknown-{msg_id.decode()}>")
                date = msg.get("Date", "")

                if mid in seen_message_ids:
                    continue
                seen_message_ids.add(mid)

                new_entries = self._extract_entries_from_message(msg, mid, date)
                entries.extend(new_entries)

            except Exception as exc:
                report.errors.append(f"Error leyendo correo {msg_id}: {exc}")

        return entries

    def _extract_entries_from_message(
        self, msg, message_id: str, message_date: str
    ) -> list[AuditEntry]:
        """Extrae AuditEntry de todos los adjuntos relevantes de un mensaje."""
        entries = []

        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if not part.get("Content-Disposition"):
                continue

            raw_name = part.get_filename() or ""
            filename = IMAPDownloader._decode_filename(raw_name)
            if not filename:
                continue

            sanitized  = IMAPDownloader._sanitize_filename(filename)
            fname_low  = sanitized.lower()
            ext        = Path(fname_low).suffix

            if ext in _AUDITED_EXTENSIONS:
                entries.append(AuditEntry(
                    filename_original=filename,
                    filename_sanitized=sanitized,
                    extension=ext,
                    source="email",
                    message_id=message_id,
                    message_date=message_date,
                ))

            elif fname_low.endswith(".zip"):
                data = part.get_payload(decode=True)
                if data:
                    zip_entries = self._extract_entries_from_zip(
                        data, filename, message_id, message_date
                    )
                    entries.extend(zip_entries)

        return entries

    def _extract_entries_from_zip(
        self, data: bytes, zip_name: str, message_id: str, message_date: str
    ) -> list[AuditEntry]:
        """Extrae AuditEntry de los archivos dentro de un ZIP adjunto."""
        entries = []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    fname     = Path(info.filename).name
                    sanitized = IMAPDownloader._sanitize_filename(fname)
                    ext       = Path(sanitized.lower()).suffix

                    if ext in _AUDITED_EXTENSIONS:
                        entries.append(AuditEntry(
                            filename_original=fname,
                            filename_sanitized=sanitized,
                            extension=ext,
                            source=f"zip:{zip_name}",
                            message_id=message_id,
                            message_date=message_date,
                        ))
        except zipfile.BadZipFile:
            pass
        return entries

    # ------------------------------------------------------------------
    # Índice de disco
    # ------------------------------------------------------------------

    def _build_disk_index(
        self, client_name: str, year: int, month: int, base_path: str
    ) -> dict[str, Path]:
        """
        Construye el índice de todos los archivos XML y PDF en disco
        para este cliente, buscando en TODA su carpeta base sin restricción
        de año ni mes.

        Esto cubre el caso de facturas de años anteriores que llegaron por
        correo en el período auditado — el sistema las guarda en su carpeta
        correcta según FechaEmision (ej: 2025/3-Mar/Facturas/), y la auditoría
        debe encontrarlas ahí aunque se esté auditando 2026.
        """
        index: dict[str, Path] = {}

        # Carpeta base del cliente (sin restricción de año)
        base = Path(base_path) / client_name
        if not base.exists():
            return index

        # Escanear recursivamente toda la carpeta del cliente
        for f in base.rglob("*"):
            if f.is_file() and Path(f.name.lower()).suffix in _AUDITED_EXTENSIONS:
                # En caso de nombres duplicados en distintas subcarpetas,
                # el primero encontrado gana (el más antiguo en el árbol)
                key = f.name.lower()
                if key not in index:
                    index[key] = f

        return index
