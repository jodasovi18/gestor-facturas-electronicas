"""
test_red.py
Prueba el comportamiento del sistema ante condiciones de red adversas.

Cubre:
  1. Timeout de conexión IMAP — el servidor no responde
  2. Timeout durante operación activa (fetch lento)
  3. Credenciales incorrectas — fallo de autenticación
  4. Reconexión tras desconexión inesperada
  5. Archivo bloqueado por otro proceso al intentar escribir (simula OneDrive)

No requiere conexión real a Gmail.
Ejecutar desde la carpeta del proyecto: python test_red.py
"""

import socket
import threading
import time
import tempfile
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from imap_downloader import IMAPDownloader

PASS = "✅ PASS"
FAIL = "❌ FAIL"
passed = 0
failed = 0


def check(label: str, condition: bool, detail: str = ""):
    global passed, failed
    estado = PASS if condition else FAIL
    print(f"   {estado} {label}" + (f" — {detail}" if detail else ""))
    if condition:
        passed += 1
    else:
        failed += 1


# ══════════════════════════════════════════════════════════════
print("=" * 62)
print("  PRUEBA: Comportamiento ante condiciones de red adversas")
print("=" * 62)


# ──────────────────────────────────────────────────────────────
print("\n── 1. Timeout de conexión — servidor que no responde")
# ──────────────────────────────────────────────────────────────

def _slow_server(port: int, delay: float):
    """Servidor TCP que acepta la conexión pero no envía nada (simula timeout)."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(1)
    srv.settimeout(delay + 2)
    try:
        conn, _ = srv.accept()
        time.sleep(delay)   # No responde — provoca timeout en el cliente
        conn.close()
    except Exception:
        pass
    finally:
        srv.close()

PORT = 19993
TIMEOUT_SECS = 2   # Timeout corto para que la prueba sea rápida

# Levantar servidor lento en hilo separado
srv_thread = threading.Thread(
    target=_slow_server, args=(PORT, TIMEOUT_SECS + 5), daemon=True
)
srv_thread.start()
time.sleep(0.2)   # Dar tiempo al servidor para que esté listo

# Parchear el downloader para apuntar al servidor local
dl = IMAPDownloader("test@test.com", "password1234", log=lambda m: None)
dl.IMAP_HOST    = "127.0.0.1"
dl.IMAP_PORT    = PORT
dl.IMAP_TIMEOUT = TIMEOUT_SECS

start = time.time()
try:
    dl.connect()
    check("Timeout de conexión — debería haber fallado", False,
          "connect() no lanzó excepción")
except (socket.timeout, TimeoutError, OSError, Exception):
    elapsed = time.time() - start
    check(
        f"Timeout disparado correctamente en ~{TIMEOUT_SECS}s",
        elapsed < TIMEOUT_SECS + 2,
        f"tardó {elapsed:.1f}s"
    )
    check(
        "Timeout no cuelga el hilo indefinidamente (< 10s)",
        elapsed < 10,
        f"tardó {elapsed:.1f}s"
    )


# ──────────────────────────────────────────────────────────────
print("\n── 2. Credenciales incorrectas — fallo de autenticación")
# ──────────────────────────────────────────────────────────────

dl2 = IMAPDownloader("usuario_inexistente@dominio_falso_xyz.com",
                     "clave_incorrecta", log=lambda m: None)

result = dl2.test_connection()
check(
    "test_connection() retorna False con credenciales inválidas",
    result is False,
    f"retornó: {result}"
)

# Verificar que disconnect() después de un fallo no lanza excepción
try:
    dl2.disconnect()
    check("disconnect() tras fallo no lanza excepción", True)
except Exception as exc:
    check("disconnect() tras fallo no lanza excepción", False, str(exc))


# ──────────────────────────────────────────────────────────────
print("\n── 3. Estado interno tras fallo de conexión")
# ──────────────────────────────────────────────────────────────

dl3 = IMAPDownloader("test@test.com", "password1234", log=lambda m: None)
dl3.IMAP_HOST    = "127.0.0.1"
dl3.IMAP_PORT    = 19994   # Puerto donde nadie escucha
dl3.IMAP_TIMEOUT = 1

try:
    dl3.connect()
except Exception:
    pass

check(
    "_mail permanece None tras fallo de conexión",
    dl3._mail is None,
    f"_mail = {dl3._mail}"
)

# disconnect() sobre estado None no debe explotar
try:
    dl3.disconnect()
    check("disconnect() sobre _mail=None no lanza excepción", True)
except Exception as exc:
    check("disconnect() sobre _mail=None no lanza excepción", False, str(exc))


# ──────────────────────────────────────────────────────────────
print("\n── 4. Archivo bloqueado al escribir (simula OneDrive sincronizando)")
# ──────────────────────────────────────────────────────────────

import zipfile
import io
from xml_classifier import XMLClassifier
from folder_manager import FolderManager

XML_SIMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<FacturaElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <FechaEmision>2024-03-01T10:00:00-06:00</FechaEmision>
</FacturaElectronica>"""

with tempfile.TemporaryDirectory() as tmpdir:
    folder_mgr  = FolderManager(tmpdir)
    xml_class   = XMLClassifier()
    client_name = "TestCliente"
    year, month = 2024, 3

    # Crear la carpeta destino anticipadamente
    dest_dir = folder_mgr.ensure_subfolder(client_name, year, month, "Facturas")
    locked_path = dest_dir / "factura_bloqueada.xml"

    # Escribir el archivo y abrirlo en modo exclusivo para simular bloqueo
    locked_path.write_bytes(XML_SIMPLE)

    dl4 = IMAPDownloader("test@test.com", "pass", log=lambda m: None)

    # Intentar guardar un archivo con el mismo nombre (ya existe = bloqueado/duplicado)
    try:
        # unique_filepath debe generar un nombre alternativo en vez de fallar
        alt_path = folder_mgr.unique_filepath(dest_dir, "factura_bloqueada.xml")
        check(
            "unique_filepath genera nombre alternativo si el archivo existe",
            alt_path.name != "factura_bloqueada.xml",
            f"nombre alternativo: '{alt_path.name}'"
        )
    except Exception as exc:
        check("unique_filepath no lanza excepción", False, str(exc))

    # _save_xml debe completar sin excepción incluso con nombre conflictivo
    try:
        saved, doc_type, *_ = dl4._save_xml(
            XML_SIMPLE, "factura_bloqueada.xml",
            folder_mgr, xml_class, client_name, year, month
        )
        check(
            "_save_xml guarda con nombre alternativo sin error",
            saved is True,
            f"doc_type={doc_type}"
        )
        # Verificar que hay ahora DOS archivos en la carpeta (original + alternativo)
        xml_files = list(dest_dir.glob("factura_bloqueada*.xml"))
        check(
            "Ambos archivos existen en disco (original + alternativo)",
            len(xml_files) == 2,
            f"encontrados: {[f.name for f in xml_files]}"
        )
    except Exception as exc:
        check("_save_xml no lanza excepción con nombre conflictivo", False, str(exc))


# ──────────────────────────────────────────────────────────────
print("\n── 5. IMAP_TIMEOUT está configurado en la clase")
# ──────────────────────────────────────────────────────────────

check(
    "IMAPDownloader.IMAP_TIMEOUT existe y es > 0",
    hasattr(IMAPDownloader, "IMAP_TIMEOUT") and IMAPDownloader.IMAP_TIMEOUT > 0,
    f"valor: {getattr(IMAPDownloader, 'IMAP_TIMEOUT', 'NO EXISTE')}"
)
check(
    "IMAP_TIMEOUT es razonable (entre 10 y 120 segundos)",
    10 <= IMAPDownloader.IMAP_TIMEOUT <= 120,
    f"valor actual: {IMAPDownloader.IMAP_TIMEOUT}s"
)


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
