"""
sistema_xml_bridge.py
Módulo de integración entre el Extractor de Facturas y el Sistema XML.

Responsabilidades:
  1. Localizar el Sistema XML en el filesystem (ruta relativa o configurable)
  2. Leer clientes_registro.json como fuente de verdad de clientes
  3. Escribir de vuelta al registro cuando se agregan/modifican clientes
     desde el Extractor (carpeta, correo de descarga)
  4. Trigger de procesamiento post-descarga vía API Flask (opcional/graceful)

El Extractor sigue funcionando normalmente si el Sistema XML no está
instalado o si el servidor Flask no está corriendo.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Localización del Sistema XML
# ---------------------------------------------------------------------------

# Ruta relativa desde el directorio del Extractor al Sistema XML.
# Ambos están en C:\Users\...\Desktop\Sistemas\, por lo que la ruta relativa
# es simplemente ../Sistema XML
_SISTEMA_XML_REL = Path(__file__).parent.parent / "Sistema XML"

# Nombre del archivo de registro maestro dentro del Sistema XML
_REGISTRO_FILENAME  = "clientes_registro.json"
_CONFIG_SYS_FILENAME = "config_sistema.json"

# Puerto por defecto del servidor Flask del Sistema XML
_FLASK_PORT = 5000
_FLASK_HOST = "127.0.0.1"


def find_sistema_xml_dir() -> Optional[Path]:
    """
    Localiza el directorio del Sistema XML.
    Busca en este orden:
      1. Ruta relativa estándar (../Sistema XML)
      2. Variable de entorno SISTEMA_XML_PATH (override para instalaciones no estándar)
    Retorna la ruta si existe, None si no se encuentra.
    """
    # Override por variable de entorno
    env_path = os.environ.get("SISTEMA_XML_PATH")
    if env_path:
        p = Path(env_path)
        if p.is_dir():
            return p

    # Ruta relativa estándar
    if _SISTEMA_XML_REL.is_dir():
        return _SISTEMA_XML_REL

    return None


def get_registro_path() -> Optional[Path]:
    """Retorna la ruta completa a clientes_registro.json, o None si no se encuentra."""
    sistema_dir = find_sistema_xml_dir()
    if not sistema_dir:
        return None
    path = sistema_dir / _REGISTRO_FILENAME
    return path if path.is_file() else None


def get_contas_root() -> Optional[str]:
    """
    Lee la ruta CONTAS raíz desde config_sistema.json del Sistema XML.
    Retorna el string de la ruta, o None si no está disponible.
    """
    sistema_dir = find_sistema_xml_dir()
    if not sistema_dir:
        return None
    config_path = sistema_dir / _CONFIG_SYS_FILENAME
    if not config_path.is_file():
        return None
    try:
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("contas_root") or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Lectura del registro de clientes
# ---------------------------------------------------------------------------

def load_registro() -> list[dict]:
    """
    Carga la lista de clientes desde clientes_registro.json.
    Retorna lista vacía si el archivo no existe o hay error de lectura.
    """
    path = get_registro_path()
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("clientes", [])
    except Exception:
        return []


def save_registro(clientes: list[dict]):
    """
    Guarda la lista de clientes de vuelta en clientes_registro.json,
    preservando los campos de metadatos (_version, _descripcion).
    """
    path = get_registro_path()
    if not path:
        raise RuntimeError(
            "No se encontró clientes_registro.json del Sistema XML. "
            "Verifique que el Sistema XML esté instalado en la ruta esperada."
        )

    # Leer la estructura existente para preservar metadatos
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {"_version": 1}

    data["clientes"] = clientes

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_client_by_email(email: str) -> Optional[dict]:
    """Busca un cliente en el registro por cualquiera de sus correos."""
    email_lower = email.strip().lower()
    for c in load_registro():
        for correo in c.get("correos", []):
            if correo.strip().lower() == email_lower:
                return c
    return None


def find_client_by_name(name: str) -> Optional[dict]:
    """Busca un cliente en el registro por nombre (case-insensitive)."""
    name_lower = name.strip().lower()
    for c in load_registro():
        if c.get("nombre", "").strip().lower() == name_lower:
            return c
    return None


def get_resolved_folder_name(email: str, default_name: str) -> str:
    """
    Devuelve el nombre de carpeta que debe usarse para este cliente.

    Si el cliente ya tiene una carpeta registrada en clientes_registro.json
    (campo 'carpeta'), retorna el nombre del último componente de esa ruta
    para garantizar consistencia entre descargas, incluso si el nombre del
    cliente fue editado posteriormente.

    Si no hay carpeta registrada (primera descarga), retorna default_name
    para que el FolderManager la cree con ese nombre.

    Esto evita que variaciones menores en el nombre del cliente (mayúsculas,
    tildes, abreviaciones) generen carpetas duplicadas.
    """
    cliente = find_client_by_email(email)
    if not cliente:
        return default_name
    carpeta = (cliente.get("carpeta") or "").strip()
    if not carpeta:
        return default_name
    folder_name = Path(carpeta).name
    return folder_name if folder_name else default_name


def update_client_folder(cedula: str, carpeta: str):
    """
    Actualiza el campo 'carpeta' de un cliente en el registro maestro.
    Se llama cuando el Extractor descarga exitosamente para un cliente,
    vinculando su carpeta de trabajo al registro.
    """
    clientes = load_registro()
    for c in clientes:
        if str(c.get("cedula", "")).strip() == cedula:
            c["carpeta"] = str(carpeta)
            save_registro(clientes)
            return
    # Cliente no encontrado — no es un error, simplemente no se actualiza


def ensure_cliente_json(carpeta_base: str, email: str):
    """
    Crea o verifica el archivo cliente.json en la carpeta base del cliente.

    El Sistema XML necesita este archivo para saber qué cédula corresponde
    al cliente y filtrar correctamente sus comprobantes. Sin él, carga todos
    los XMLs de la carpeta sin filtrar.

    Se crea automáticamente la primera vez que el Extractor descarga para
    un cliente que está en el registro del Sistema XML.

    La estructura del cliente.json es la que espera parse_xml.py:
      { "nombre": "...", "cedula": "...", "tipo_cedula": "..." }
    """
    import re as _re

    carpeta_path = Path(carpeta_base)
    if not carpeta_path.is_dir():
        return   # carpeta aún no existe, no crear

    cliente_json_path = carpeta_path / "cliente.json"
    if cliente_json_path.exists():
        return   # ya existe, no sobreescribir

    # Buscar el cliente en el registro por email
    email_lower = email.strip().lower()
    clientes = load_registro()
    cliente_reg = None
    for c in clientes:
        for correo in c.get("correos", []):
            if correo.strip().lower() == email_lower:
                cliente_reg = c
                break
        if cliente_reg:
            break

    if not cliente_reg:
        return   # no está en el registro, no crear

    cedula  = str(cliente_reg.get("cedula", "")).strip()
    nombre  = cliente_reg.get("nombre", "").strip()

    if not cedula or not nombre:
        return   # datos insuficientes

    # Determinar tipo de cédula a partir del formato:
    # 9 dígitos → física, 10 dígitos → jurídica, otro → jurídica por defecto
    cedula_digits = _re.sub(r"[^0-9]", "", cedula)
    if len(cedula_digits) == 9:
        tipo_cedula = "fisica"
    else:
        tipo_cedula = "juridica"

    cliente_obj = {
        "nombre":      nombre,
        "cedula":      cedula,
        "tipo_cedula": tipo_cedula,
    }

    try:
        with open(cliente_json_path, "w", encoding="utf-8") as f:
            json.dump(cliente_obj, f, ensure_ascii=False, indent=2)
    except OSError:
        pass   # no bloquear si falla la escritura


def add_email_to_client(cedula: str, email: str):
    """
    Agrega un correo a la lista de correos de un cliente si no existe ya.
    Se usa cuando se agrega un cliente desde el Extractor que no estaba
    en el registro pero queremos enriquecer su entrada.
    """
    clientes = load_registro()
    for c in clientes:
        if str(c.get("cedula", "")).strip() == cedula:
            emails = c.setdefault("correos", [])
            if email.lower() not in [e.lower() for e in emails]:
                emails.append(email)
                save_registro(clientes)
            return


# ---------------------------------------------------------------------------
# Conversión entre modelos de datos
# ---------------------------------------------------------------------------

def registro_to_extractor_client(reg_client: dict, email: str,
                                  tipo_override: str = "") -> dict:
    """
    Convierte un cliente del registro del Sistema XML al formato del Extractor.
    El Extractor necesita: name, email, password (en keyring), tipo (IVA/REA).

    'email' es el correo Gmail específico que se usará para la descarga IMAP.
    'tipo_override' permite forzar IVA/REA si el registro no lo tiene.
    """
    # El régimen en el registro puede ser 'IVA', 'REA' o '' (vacío)
    regimen = reg_client.get("regimen", "").upper().strip()
    if regimen not in ("IVA", "REA"):
        regimen = tipo_override or "IVA"   # fallback a IVA

    return {
        "name":     reg_client.get("nombre", "").strip(),
        "email":    email.strip().lower(),
        "password": "",   # placeholder — la contraseña viene del keyring
        "tipo":     regimen,
        # Campos extra del registro (no usados por el Extractor pero preservados)
        "_cedula":  reg_client.get("cedula", ""),
    }


def extractor_to_registro_client(ext_client: dict) -> dict:
    """
    Crea una entrada mínima de registro a partir de un cliente del Extractor.
    Se usa cuando se agrega un cliente nuevo desde el Extractor que no
    existe aún en el registro del Sistema XML.
    """
    return {
        "cedula":      ext_client.get("_cedula", ""),
        "nombre":      ext_client.get("name", "").strip(),
        "regimen":     ext_client.get("tipo", "IVA"),
        "correos":     [ext_client["email"]] if ext_client.get("email") else [],
        "telefono":    "",
        "encargados":  [],
        "comentarios": "Agregado desde el Extractor de Facturas.",
        "carpeta":     "",
    }


# ---------------------------------------------------------------------------
# Verificación del servidor Flask
# ---------------------------------------------------------------------------

def is_flask_running(timeout: float = 1.0) -> bool:
    """
    Verifica si el servidor Flask del Sistema XML está corriendo.
    Usa una conexión TCP simple — no hace HTTP, solo verifica que el puerto
    está escuchando. Timeout corto para no bloquear la UI.
    """
    try:
        with socket.create_connection((_FLASK_HOST, _FLASK_PORT),
                                      timeout=timeout):
            return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False


def trigger_procesamiento(carpeta_cyg: str, periodo: str) -> dict:
    """
    Llama a la API del Sistema XML para procesar una carpeta CyG.
    Si el servidor no está corriendo, retorna sin error (graceful).

    carpeta_cyg: ruta completa a la carpeta CyG del cliente
                 ej: C:\\...\\CONTAS\\IVA\\Cliente\\2026\\3-Mar\\CyG
    periodo:     formato YYYYMM, ej: '202603'

    Retorna dict con:
      { 'ok': bool, 'triggered': bool, 'message': str }
    """
    if not is_flask_running():
        return {
            "ok":        True,
            "triggered": False,
            "message":   "Sistema XML no está corriendo — procesamiento omitido.",
        }

    try:
        import urllib.request
        import urllib.error

        # 1. Establecer carpeta activa
        payload_config = json.dumps({"carpeta": carpeta_cyg}).encode("utf-8")
        req_config = urllib.request.Request(
            f"http://{_FLASK_HOST}:{_FLASK_PORT}/api/config",
            data=payload_config,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_config, timeout=5) as resp:
            result_config = json.loads(resp.read())

        if not result_config.get("ok"):
            return {
                "ok":        False,
                "triggered": False,
                "message":   f"Error al configurar carpeta: {result_config.get('error')}",
            }

        # 2. Iniciar procesamiento
        payload_proc = json.dumps({"periodo": periodo}).encode("utf-8")
        req_proc = urllib.request.Request(
            f"http://{_FLASK_HOST}:{_FLASK_PORT}/api/procesar",
            data=payload_proc,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req_proc, timeout=5) as resp:
            result_proc = json.loads(resp.read())

        return {
            "ok":        True,
            "triggered": True,
            "message":   "Procesamiento iniciado en el Sistema XML.",
            "detail":    result_proc,
        }

    except urllib.error.URLError as exc:
        return {
            "ok":        True,   # no es error del Extractor
            "triggered": False,
            "message":   f"No se pudo conectar al Sistema XML: {exc.reason}",
        }
    except Exception as exc:
        return {
            "ok":        True,
            "triggered": False,
            "message":   f"Error inesperado al contactar el Sistema XML: {exc}",
        }


def open_in_sistema_xml(carpeta_cyg: str):
    """
    Abre el Sistema XML en el navegador con la carpeta del cliente ya activa.
    Primero establece la carpeta vía API, luego abre la URL.
    Si el servidor no está corriendo, abre solo la URL raíz.
    """
    import webbrowser

    if is_flask_running():
        try:
            import urllib.request
            payload = json.dumps({"carpeta": carpeta_cyg}).encode("utf-8")
            req = urllib.request.Request(
                f"http://{_FLASK_HOST}:{_FLASK_PORT}/api/config",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass   # Si falla, igual abrimos el navegador

    webbrowser.open(f"http://{_FLASK_HOST}:{_FLASK_PORT}")


# ---------------------------------------------------------------------------
# Estado del bridge (para mostrar en la UI)
# ---------------------------------------------------------------------------

def get_bridge_status() -> dict:
    """
    Retorna el estado completo del bridge para mostrarlo en la UI del Extractor.
    """
    sistema_dir   = find_sistema_xml_dir()
    registro_path = get_registro_path()
    clientes      = load_registro() if registro_path else []
    contas_root   = get_contas_root()
    flask_running = is_flask_running(timeout=0.5)

    return {
        "sistema_xml_encontrado": sistema_dir is not None,
        "sistema_xml_dir":        str(sistema_dir) if sistema_dir else None,
        "registro_encontrado":    registro_path is not None,
        "total_clientes":         len(clientes),
        "contas_root":            contas_root,
        "flask_running":          flask_running,
    }
