"""
test_adjuntos_inusuales.py
Prueba el procesamiento de adjuntos con estructura de correo no estándar.

Cubre:
  1. Adjunto sin Content-Disposition (solo Content-Type con name=)
  2. Adjunto con disposition=inline en lugar de attachment
  3. Correo reenviado — adjunto dentro de message/rfc822
  4. ZIP dentro de ZIP (anidamiento de un nivel)
  5. ZIP corrupto — no debe interrumpir el procesamiento
  6. Adjunto con payload vacío (data = None o b'')
  7. Múltiples adjuntos en el mismo correo — todos deben procesarse
  8. Nombre de adjunto solo en Content-Type (sin Content-Disposition)

No requiere conexión a Gmail — construye mensajes MIME sintéticos.
Ejecutar desde la carpeta del proyecto: python test_adjuntos_inusuales.py
"""

import email
import zipfile
import io
import tempfile
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email.mime.message import MIMEMessage
from email import encoders
from pathlib import Path

from imap_downloader import IMAPDownloader
from xml_classifier import XMLClassifier
from folder_manager import FolderManager

PASS = "✅ PASS"
FAIL = "❌ FAIL"
passed = 0
failed = 0

# ── XMLs y PDFs de prueba ──────────────────────────────────────
XML_FACTURA = b"""<?xml version="1.0" encoding="UTF-8"?>
<FacturaElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <FechaEmision>2024-05-10T09:00:00-06:00</FechaEmision>
</FacturaElectronica>"""

XML_TIQUETE = b"""<?xml version="1.0" encoding="UTF-8"?>
<TiqueteElectronico xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <FechaEmision>2024-05-12T14:00:00-06:00</FechaEmision>
</TiqueteElectronico>"""

XML_NOTA = b"""<?xml version="1.0" encoding="UTF-8"?>
<NotaCreditoElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <FechaEmision>2024-05-15T11:00:00-06:00</FechaEmision>
</NotaCreditoElectronica>"""

PDF_DUMMY = b"%PDF-1.4 dummy content for testing purposes"


def check(label: str, condition: bool, detail: str = ""):
    global passed, failed
    estado = PASS if condition else FAIL
    print(f"   {estado} {label}" + (f" — {detail}" if detail else ""))
    if condition:
        passed += 1
    else:
        failed += 1


def make_downloader(tmpdir: str) -> tuple:
    """Crea un IMAPDownloader y sus dependencias apuntando a tmpdir."""
    dl         = IMAPDownloader("test@test.com", "pass", log=lambda m: None)
    folder_mgr = FolderManager(tmpdir)
    xml_class  = XMLClassifier()
    return dl, folder_mgr, xml_class


def count_files(tmpdir: str, client: str, year: int, month: int) -> dict:
    """
    Cuenta archivos por subcarpeta en la ruta del mes.
    Retorna dict {subcarpeta: cantidad}.
    """
    from folder_manager import FolderManager as FM
    month_path = Path(tmpdir) / client / str(year) / f"{month}-{FM.MONTHS_ES[month]}"
    result = {}
    if month_path.exists():
        for sub in month_path.iterdir():
            if sub.is_dir():
                result[sub.name] = len(list(sub.glob("*")))
    return result


def make_attachment(data: bytes, filename: str,
                    disposition: str = "attachment",
                    use_content_type_name: bool = False) -> MIMEBase:
    """Crea un adjunto MIME con las opciones indicadas."""
    part = MIMEBase("application", "octet-stream")
    part.set_payload(data)
    encoders.encode_base64(part)
    if use_content_type_name:
        # Nombre solo en Content-Type, sin Content-Disposition
        part.set_type("application/xml")
        part.set_param("name", filename, header="Content-Type")
        # No agregar Content-Disposition
    else:
        part.add_header("Content-Disposition", disposition, filename=filename)
    return part


# ══════════════════════════════════════════════════════════════
print("=" * 62)
print("  PRUEBA: Adjuntos con estructura de correo inusual")
print("=" * 62)

YEAR, MONTH, CLIENT = 2024, 5, "ClientePrueba"


# ──────────────────────────────────────────────────────────────
print("\n── 1. Adjunto sin Content-Disposition (nombre solo en Content-Type)")
# ──────────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmpdir:
    dl, fm, xc = make_downloader(tmpdir)

    msg = MIMEMultipart()
    msg["Message-ID"] = "<test1@test>"
    # Adjunto con nombre solo en Content-Type, sin Content-Disposition
    msg.attach(make_attachment(XML_FACTURA, "factura_sin_disp.xml",
                               use_content_type_name=True))

    parsed = email.message_from_bytes(msg.as_bytes())
    existing: set = set()
    count, skipped, types, errors = dl._process_attachments(
        parsed, fm, xc, CLIENT, YEAR, MONTH, existing
    )

    check("Adjunto sin Content-Disposition — detectado y procesado",
          count == 1, f"count={count}, errors={errors}")
    check("Sin Content-Disposition — guardado como Factura",
          "Facturas" in types, f"types={types}")


# ──────────────────────────────────────────────────────────────
print("\n── 2. Adjunto con disposition=inline")
# ──────────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmpdir:
    dl, fm, xc = make_downloader(tmpdir)

    msg = MIMEMultipart()
    msg["Message-ID"] = "<test2@test>"
    msg.attach(make_attachment(XML_TIQUETE, "tiquete_inline.xml",
                               disposition="inline"))

    parsed = email.message_from_bytes(msg.as_bytes())
    existing: set = set()
    count, skipped, types, errors = dl._process_attachments(
        parsed, fm, xc, CLIENT, YEAR, MONTH, existing
    )

    check("Adjunto inline — detectado y procesado",
          count == 1, f"count={count}, errors={errors}")
    check("Adjunto inline — guardado como Tiquete",
          "Tiquetes" in types, f"types={types}")


# ──────────────────────────────────────────────────────────────
print("\n── 3. Correo reenviado — adjunto dentro de message/rfc822")
# ──────────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmpdir:
    dl, fm, xc = make_downloader(tmpdir)

    # Construir el mensaje original (inner) con el XML adjunto
    inner = MIMEMultipart()
    inner["Message-ID"] = "<original@test>"
    inner["Subject"]    = "Factura original"
    inner.attach(make_attachment(XML_NOTA, "nota_credito_reenviada.xml"))

    # Construir el correo reenviado que envuelve al original
    outer = MIMEMultipart()
    outer["Message-ID"] = "<fwd@test>"
    outer["Subject"]    = "Fwd: Factura original"
    outer.attach(MIMEText("Te reenvío esta factura.", "plain"))
    outer.attach(MIMEMessage(inner))   # El original como message/rfc822

    parsed = email.message_from_bytes(outer.as_bytes())
    existing: set = set()
    count, skipped, types, errors = dl._process_attachments(
        parsed, fm, xc, CLIENT, YEAR, MONTH, existing
    )

    check("Correo reenviado — adjunto del mensaje original procesado",
          count == 1, f"count={count}, errors={errors}")
    check("Correo reenviado — guardado como Nota de Crédito",
          "Notas de Crédito" in types, f"types={types}")


# ──────────────────────────────────────────────────────────────
print("\n── 4. ZIP dentro de ZIP (anidamiento)")
# ──────────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmpdir:
    dl, fm, xc = make_downloader(tmpdir)

    # Crear ZIP interno con un XML
    inner_zip_buf = io.BytesIO()
    with zipfile.ZipFile(inner_zip_buf, "w") as inner_zf:
        inner_zf.writestr("factura_en_zip_interno.xml", XML_FACTURA)
    inner_zip_bytes = inner_zip_buf.getvalue()

    # Crear ZIP externo que contiene el ZIP interno + un XML directo
    outer_zip_buf = io.BytesIO()
    with zipfile.ZipFile(outer_zip_buf, "w") as outer_zf:
        outer_zf.writestr("comprobantes_internos.zip", inner_zip_bytes)
        outer_zf.writestr("tiquete_directo.xml", XML_TIQUETE)
    outer_zip_bytes = outer_zip_buf.getvalue()

    existing: set = set()
    count, skipped, types = dl._extract_zip(
        outer_zip_bytes, fm, xc, CLIENT, YEAR, MONTH, existing
    )

    check("ZIP anidado — XML del ZIP interno extraído",
          count >= 2, f"count={count} (esperado ≥ 2)")
    check("ZIP anidado — Factura del ZIP interno guardada",
          "Facturas" in types, f"types={types}")
    check("ZIP anidado — Tiquete del ZIP externo guardado",
          "Tiquetes" in types, f"types={types}")


# ──────────────────────────────────────────────────────────────
print("\n── 5. ZIP corrupto — no interrumpe el procesamiento")
# ──────────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmpdir:
    dl, fm, xc = make_downloader(tmpdir)

    msg = MIMEMultipart()
    msg["Message-ID"] = "<test5@test>"
    # ZIP corrupto
    msg.attach(make_attachment(b"esto no es un zip valido <<<",
                               "comprobantes_corruptos.zip"))
    # XML válido en el mismo correo
    msg.attach(make_attachment(XML_FACTURA, "factura_valida.xml"))

    parsed = email.message_from_bytes(msg.as_bytes())
    existing: set = set()

    try:
        count, skipped, types, errors = dl._process_attachments(
            parsed, fm, xc, CLIENT, YEAR, MONTH, existing
        )
        check("ZIP corrupto — no lanza excepción",
              True)
        check("ZIP corrupto — XML válido del mismo correo sí se procesa",
              count == 1 and "Facturas" in types,
              f"count={count}, types={types}")
    except Exception as exc:
        check("ZIP corrupto — no lanza excepción", False, str(exc))


# ──────────────────────────────────────────────────────────────
print("\n── 6. Adjunto con payload vacío")
# ──────────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmpdir:
    dl, fm, xc = make_downloader(tmpdir)

    msg = MIMEMultipart()
    msg["Message-ID"] = "<test6@test>"
    # Adjunto con datos vacíos
    msg.attach(make_attachment(b"", "vacio.xml"))
    # PDF válido en el mismo correo
    msg.attach(make_attachment(PDF_DUMMY, "comprobante.pdf"))

    parsed = email.message_from_bytes(msg.as_bytes())
    existing: set = set()

    try:
        count, skipped, types, errors = dl._process_attachments(
            parsed, fm, xc, CLIENT, YEAR, MONTH, existing
        )
        check("Payload vacío — no lanza excepción", True)
        check("Payload vacío — PDF del mismo correo sí se procesa",
              "PDFs" in types, f"types={types}, count={count}")
    except Exception as exc:
        check("Payload vacío — no lanza excepción", False, str(exc))


# ──────────────────────────────────────────────────────────────
print("\n── 7. Múltiples adjuntos en el mismo correo")
# ──────────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmpdir:
    dl, fm, xc = make_downloader(tmpdir)

    msg = MIMEMultipart()
    msg["Message-ID"] = "<test7@test>"
    msg.attach(make_attachment(XML_FACTURA, "factura_001.xml"))
    msg.attach(make_attachment(XML_TIQUETE, "tiquete_001.xml"))
    msg.attach(make_attachment(XML_NOTA,    "nota_001.xml"))
    msg.attach(make_attachment(PDF_DUMMY,   "comprobante_001.pdf"))

    parsed = email.message_from_bytes(msg.as_bytes())
    existing: set = set()
    count, skipped, types, errors = dl._process_attachments(
        parsed, fm, xc, CLIENT, YEAR, MONTH, existing
    )

    check("Múltiples adjuntos — todos procesados",
          count == 4, f"count={count} (esperado 4)")
    check("Múltiples adjuntos — Facturas presente",   "Facturas" in types)
    check("Múltiples adjuntos — Tiquetes presente",   "Tiquetes" in types)
    check("Múltiples adjuntos — Notas presente",      "Notas de Crédito" in types)
    check("Múltiples adjuntos — PDFs presente",       "PDFs" in types)
    check("Múltiples adjuntos — sin errores",
          errors == 0, f"errors={errors}")


# ──────────────────────────────────────────────────────────────
print("\n── 8. Anti-duplicados dentro del mismo correo")
# ──────────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmpdir:
    dl, fm, xc = make_downloader(tmpdir)

    msg = MIMEMultipart()
    msg["Message-ID"] = "<test8@test>"
    # El mismo archivo adjuntado dos veces (ocurre en algunos reenvíos)
    msg.attach(make_attachment(XML_FACTURA, "factura_dup.xml"))
    msg.attach(make_attachment(XML_FACTURA, "factura_dup.xml"))

    parsed = email.message_from_bytes(msg.as_bytes())
    existing: set = set()
    count, skipped, types, errors = dl._process_attachments(
        parsed, fm, xc, CLIENT, YEAR, MONTH, existing
    )

    check("Mismo adjunto dos veces — solo se guarda una vez",
          count == 1, f"count={count}")
    check("Mismo adjunto dos veces — el segundo se omite",
          skipped == 1, f"skipped={skipped}")


# ──────────────────────────────────────────────────────────────
print(f"\n{'=' * 62}")
print(f"  Resultado: {passed} pasaron, {failed} fallaron "
      f"de {passed + failed} verificaciones")
print("=" * 62)
if failed == 0:
    print("  🎉 Todos los casos pasaron.")
else:
    print("  ⚠️  Revisá los casos marcados con ❌ arriba.")
print()
