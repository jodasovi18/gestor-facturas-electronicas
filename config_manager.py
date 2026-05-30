"""
config_manager.py
Gestión de configuración y clientes del Gestor de Facturas Electrónicas.
Almacena la configuración en un archivo JSON local.

Seguridad de contraseñas
------------------------
Las contraseñas se almacenan en el Administrador de Credenciales del sistema
operativo (Windows Credential Manager) mediante el módulo `keyring`, que las
cifra con las credenciales del usuario de Windows. El archivo config.json
NO contiene contraseñas en texto claro ni en base64.

Si `keyring` no está disponible (entorno sin GUI o SO no soportado), se cae
automáticamente al esquema base64 anterior como fallback, con una advertencia.
"""

from __future__ import annotations   # compatibilidad con Python 3.9

import json
import os
import base64
import warnings
from pathlib import Path

try:
    import keyring
    from keyring.errors import KeyringError
    _KEYRING_AVAILABLE = True
except ImportError:
    _KEYRING_AVAILABLE = False

# Integración opcional con el Sistema XML
try:
    from sistema_xml_bridge import (
        load_registro, save_registro, find_client_by_email,
        find_client_by_name, update_client_folder, add_email_to_client,
        extractor_to_registro_client, get_bridge_status,
        ensure_cliente_json,
    )
    _BRIDGE_AVAILABLE = True
except ImportError:
    _BRIDGE_AVAILABLE = False

# Nombre del servicio en el Administrador de Credenciales de Windows
_KEYRING_SERVICE = "GestorFacturas"

# Sentinel que indica que la contraseña está en keyring (no en el JSON)
_PWD_IN_KEYRING = "__keyring__"


class ConfigManager:
    """Maneja la configuración persistente de la aplicación."""

    CONFIG_DIR  = Path.home() / "AppData" / "Local" / "GestorFacturas"
    CONFIG_FILE = CONFIG_DIR / "config.json"

    DEFAULT_CONFIG = {
        "base_path_iva":    "",
        "base_path_rea":    "",
        "clients":          [],
        "default_folder":   "CyG",
        "download_history": [],   # lista de entradas de descarga completadas
    }

    def __init__(self):
        self.config = self._load()

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self) -> dict:
        if self.CONFIG_FILE.exists():
            try:
                with open(self.CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # Migración: si existe la clave antigua "base_path", pasarla a base_path_iva
                if "base_path" in data and not data.get("base_path_iva"):
                    data["base_path_iva"] = data.pop("base_path")
                elif "base_path" in data:
                    data.pop("base_path")

                # Migración: si algún cliente no tiene campo "tipo", asignar "IVA" por defecto
                for client in data.get("clients", []):
                    client.setdefault("tipo", "IVA")

                # Migración: contraseñas base64 → keyring
                # Si keyring está disponible y el cliente tiene contraseña en base64
                # (distinta de __keyring__), moverla al keyring del SO automáticamente.
                if _KEYRING_AVAILABLE:
                    migrated = False
                    for client in data.get("clients", []):
                        pwd_field = client.get("password", "")
                        if pwd_field and pwd_field != _PWD_IN_KEYRING:
                            try:
                                plain = base64.b64decode(
                                    pwd_field.encode("utf-8")
                                ).decode("utf-8")
                                keyring.set_password(
                                    _KEYRING_SERVICE, client["email"], plain
                                )
                                client["password"] = _PWD_IN_KEYRING
                                migrated = True
                            except Exception:
                                pass  # Si falla, se queda en base64
                    if migrated:
                        # Guardar config actualizada con los sentinels
                        try:
                            self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                            with open(self.CONFIG_FILE, "w", encoding="utf-8") as _f:
                                json.dump(data, _f, ensure_ascii=False, indent=2)
                        except OSError:
                            pass

                # Asegurar que existen todas las claves por defecto
                for key, val in self.DEFAULT_CONFIG.items():
                    data.setdefault(key, val)

                return data
            except (json.JSONDecodeError, OSError):
                pass
        return dict(self.DEFAULT_CONFIG)

    def save(self):
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # Rutas base (IVA y REA)
    # ------------------------------------------------------------------

    def get_base_path_iva(self) -> str:
        return self.config.get("base_path_iva", "")

    def get_base_path_rea(self) -> str:
        return self.config.get("base_path_rea", "")

    def set_base_path_iva(self, path: str):
        self.config["base_path_iva"] = path.strip()
        self.save()

    def set_base_path_rea(self, path: str):
        self.config["base_path_rea"] = path.strip()
        self.save()

    def get_base_path_for_client(self, client: dict) -> str:
        """Devuelve la ruta base correcta según el tipo del cliente (IVA o REA)."""
        tipo = client.get("tipo", "IVA")
        if tipo == "REA":
            return self.get_base_path_rea()
        return self.get_base_path_iva()

    # ------------------------------------------------------------------
    # Clientes
    # ------------------------------------------------------------------

    def get_clients(self) -> list:
        """
        Retorna la lista de clientes para el Extractor.

        Si el Sistema XML está disponible, lee del registro maestro
        (clientes_registro.json) y la enriquece con las contraseñas Gmail
        almacenadas en keyring. Los clientes del registro que no tengan
        correo configurado se omiten (no se pueden usar para descarga IMAP).

        Si el Sistema XML no está disponible, retorna la lista local del
        config.json como fallback (comportamiento original).
        """
        if _BRIDGE_AVAILABLE:
            return self._get_clients_from_registro()
        return self.config.get("clients", [])

    def _get_clients_from_registro(self) -> list:
        """
        Construye la lista de clientes fusionando el registro del Sistema XML
        con las credenciales Gmail del config local y el keyring.

        Reglas de fusión:
        - Fuente de verdad para nombre, régimen: registro del Sistema XML
        - Fuente de verdad para contraseña Gmail: keyring (por email)
        - Clientes sin correo en el registro → se omiten
        - Clientes con múltiples correos → se generan múltiples entradas,
          una por correo (cada correo es una cuenta Gmail independiente)
        - El campo 'tipo' se mapea desde 'regimen': IVA→IVA, REA→REA, ''→IVA
        """
        registro = load_registro()
        resultado = []

        for reg_client in registro:
            correos = reg_client.get("correos", [])
            if not correos:
                continue

            nombre  = reg_client.get("nombre", "").strip()
            regimen = reg_client.get("regimen", "").upper().strip()
            tipo    = regimen if regimen in ("IVA", "REA") else "IVA"
            cedula  = reg_client.get("cedula", "")

            for email in correos:
                email = email.strip().lower()
                if not email:
                    continue

                # Buscar contraseña en keyring o en config local
                pwd_encoded = self._find_password_for_email(email)

                # Solo incluir si tiene contraseña configurada
                # (cliente sin contraseña no puede usarse para descarga)
                if not pwd_encoded:
                    continue

                # Nombre de entrada: si hay múltiples correos, indicar cuál
                display_name = nombre
                if len(correos) > 1:
                    display_name = f"{nombre} ({email.split('@')[0]})"

                resultado.append({
                    "name":     display_name,
                    "email":    email,
                    "password": pwd_encoded,
                    "tipo":     tipo,
                    "_cedula":  cedula,   # campo interno para sync con registro
                })

        # Agregar clientes locales que no están en el registro
        # (clientes que el usuario agregó desde el Extractor sin cédula)
        for local_client in self.config.get("clients", []):
            email = local_client.get("email", "").lower()
            already_included = any(
                c["email"] == email for c in resultado
            )
            if not already_included:
                resultado.append(local_client)

        return resultado

    def _find_password_for_email(self, email: str) -> str:
        """
        Busca la contraseña para un email en este orden:
        1. Keyring del SO (más seguro)
        2. Config local (base64, legacy)
        Retorna el valor codificado a almacenar, o '' si no existe.
        """
        # Buscar en keyring
        if _KEYRING_AVAILABLE:
            try:
                pwd = keyring.get_password(_KEYRING_SERVICE, email)
                if pwd:
                    return _PWD_IN_KEYRING
            except Exception:
                pass

        # Buscar en config local
        for c in self.config.get("clients", []):
            if c.get("email", "").lower() == email:
                return c.get("password", "")

        return ""

    def add_client(self, name: str, email: str, password: str, tipo: str = "IVA"):
        """
        Agrega un cliente.
        - Contraseña → keyring del SO (o base64 como fallback)
        - Si el bridge está disponible, busca o crea la entrada en
          clientes_registro.json del Sistema XML
        - Siempre guarda también en config local para consistencia
        """
        stored_pwd = self._store_password(email, password)

        # Sincronizar con registro del Sistema XML
        if _BRIDGE_AVAILABLE:
            self._sync_add_to_registro(name, email, tipo)

        # Guardar en config local (necesario para clientes sin cédula
        # y como fuente de las contraseñas cuando no hay bridge)
        self.config["clients"].append({
            "name":     name,
            "email":    email,
            "password": stored_pwd,
            "tipo":     tipo,
        })
        self.save()

    def _sync_add_to_registro(self, name: str, email: str, tipo: str):
        """
        Agrega o actualiza el cliente en el registro del Sistema XML.
        Si ya existe por email → agrega el email si no estaba.
        Si ya existe por nombre → agrega el email si no estaba.
        Si no existe → crea entrada nueva sin cédula.
        """
        try:
            clientes = load_registro()
            email_lower = email.strip().lower()

            # Buscar por email
            for c in clientes:
                emails_lower = [e.strip().lower() for e in c.get("correos", [])]
                if email_lower in emails_lower:
                    return   # ya existe, nada que hacer

            # Buscar por nombre
            name_lower = name.strip().lower()
            for c in clientes:
                if c.get("nombre", "").strip().lower() == name_lower:
                    # Agregar el email a este cliente
                    correos = c.setdefault("correos", [])
                    if email_lower not in [e.lower() for e in correos]:
                        correos.append(email)
                    # Actualizar régimen si estaba vacío
                    if not c.get("regimen") and tipo in ("IVA", "REA"):
                        c["regimen"] = tipo
                    save_registro(clientes)
                    return

            # No existe — crear entrada nueva
            nuevo = {
                "cedula":      "",
                "nombre":      name.strip(),
                "regimen":     tipo if tipo in ("IVA", "REA") else "IVA",
                "correos":     [email],
                "telefono":    "",
                "encargados":  [],
                "comentarios": "Agregado desde el Extractor de Facturas.",
                "carpeta":     "",
            }
            clientes.append(nuevo)
            save_registro(clientes)
        except Exception as exc:
            # La sincronización nunca debe bloquear la operación principal
            warnings.warn(f"No se pudo sincronizar con el Sistema XML: {exc}",
                          stacklevel=4)

    def update_client(self, index: int, name: str, email: str,
                      password: str, tipo: str = "IVA"):
        """Actualiza los datos de un cliente existente."""
        old_email = self.config["clients"][index].get("email", "") \
            if index < len(self.config["clients"]) else ""

        if old_email and old_email != email and _KEYRING_AVAILABLE:
            self._delete_keyring(old_email)

        stored_pwd = self._store_password(email, password)

        # Actualizar en config local
        if index < len(self.config["clients"]):
            self.config["clients"][index] = {
                "name":     name,
                "email":    email,
                "password": stored_pwd,
                "tipo":     tipo,
            }
            self.save()

        # Sincronizar régimen con el registro si cambió
        if _BRIDGE_AVAILABLE and tipo in ("IVA", "REA"):
            try:
                clientes = load_registro()
                for c in clientes:
                    emails_lower = [e.strip().lower() for e in c.get("correos", [])]
                    if email.lower() in emails_lower:
                        if c.get("regimen") != tipo:
                            c["regimen"] = tipo
                            save_registro(clientes)
                        break
            except Exception as exc:
                warnings.warn(f"No se pudo sincronizar régimen con el Sistema XML: {exc}",
                              stacklevel=3)

    def remove_client(self, index: int):
        """Elimina un cliente y su contraseña del keyring."""
        client = self.config["clients"][index]
        if _KEYRING_AVAILABLE:
            self._delete_keyring(client.get("email", ""))
        self.config["clients"].pop(index)
        self.save()

    def decode_password(self, encoded: str, email: str = "") -> str:
        """
        Devuelve la contraseña del cliente.
        - Si el valor guardado es '__keyring__', la lee desde el keyring del SO.
        - Si es base64 (migración de configs antiguas), la decodifica directamente.
        """
        if encoded == _PWD_IN_KEYRING:
            if not _KEYRING_AVAILABLE:
                raise RuntimeError(
                    "keyring no disponible pero la contraseña requiere keyring. "
                    "Instale el paquete: pip install keyring"
                )
            if not email:
                raise ValueError("Se requiere el email para leer la contraseña del keyring.")
            try:
                pwd = keyring.get_password(_KEYRING_SERVICE, email)
                if pwd is None:
                    raise RuntimeError(
                        f"No se encontró la contraseña para '{email}' en el "
                        "Administrador de Credenciales. Edite el cliente y guárdela nuevamente."
                    )
                return pwd
            except KeyringError as exc:
                raise RuntimeError(f"Error al leer keyring para '{email}': {exc}") from exc

        # Fallback: base64 (configs anteriores a esta versión)
        try:
            return base64.b64decode(encoded.encode("utf-8")).decode("utf-8")
        except Exception as exc:
            raise ValueError(f"No se puede decodificar la contraseña almacenada: {exc}") from exc

    # ------------------------------------------------------------------
    # Helpers internos de keyring
    # ------------------------------------------------------------------

    def _store_password(self, email: str, password: str) -> str:
        """
        Guarda la contraseña en keyring si está disponible.
        Retorna el valor a almacenar en el JSON ('__keyring__' o base64).
        """
        if _KEYRING_AVAILABLE:
            try:
                keyring.set_password(_KEYRING_SERVICE, email, password)
                return _PWD_IN_KEYRING
            except KeyringError as exc:
                warnings.warn(
                    f"No se pudo usar keyring ({exc}). "
                    "La contraseña se almacenará en base64 como fallback.",
                    stacklevel=3,
                )
        # Fallback base64
        return base64.b64encode(password.encode("utf-8")).decode("utf-8")

    @staticmethod
    def _delete_keyring(email: str):
        """Elimina la entrada del keyring para el email dado (silencia errores)."""
        if not email or not _KEYRING_AVAILABLE:
            return
        try:
            keyring.delete_password(_KEYRING_SERVICE, email)
        except Exception:
            pass

    def email_exists_for_other(self, email: str, exclude_email: str = "") -> bool:
        """
        Retorna True si el correo ya tiene credenciales configuradas
        para un cliente DISTINTO al excluido.
        Se usa en edición para detectar conflictos reales.
        """
        email_lower = email.lower().strip()
        for c in self.config.get("clients", []):
            if c["email"] == email_lower and c["email"] != exclude_email.lower():
                return True
        if _KEYRING_AVAILABLE:
            try:
                pwd = keyring.get_password(_KEYRING_SERVICE, email_lower)
                if pwd:
                    # Verificar que no sea el propio cliente excluido
                    if email_lower != exclude_email.lower():
                        return True
            except Exception:
                pass
        return False

    def email_exists(self, email: str) -> bool:
        """
        Retorna True si el correo ya tiene credenciales configuradas
        (contraseña en keyring o en config local).
        Correos compartidos entre clientes son válidos — esta función
        solo detecta si el correo ya fue registrado antes.
        """
        email_lower = email.lower().strip()

        # Buscar en config local
        if any(c["email"] == email_lower
               for c in self.config.get("clients", [])):
            return True

        # Verificar keyring
        if _KEYRING_AVAILABLE:
            try:
                pwd = keyring.get_password(_KEYRING_SERVICE, email_lower)
                if pwd:
                    return True
            except Exception:
                pass

        return False

    def name_exists(self, name: str) -> bool:
        """
        Retorna True solo si ya existe un cliente con ese nombre
        que tenga credenciales configuradas (en config local o con
        contraseña en keyring).

        Un nombre que existe en el registro del Sistema XML pero sin
        contraseña configurada NO se considera duplicado.
        """
        name_lower = name.strip().lower()

        # Buscar en config local
        if any(c["name"].lower() == name_lower
               for c in self.config.get("clients", [])):
            return True

        # Verificar si algún cliente del registro con ese nombre
        # ya tiene contraseña en keyring
        if _BRIDGE_AVAILABLE and _KEYRING_AVAILABLE:
            try:
                cliente = find_client_by_name(name_lower)
                if cliente:
                    for correo in cliente.get("correos", []):
                        pwd = keyring.get_password(_KEYRING_SERVICE,
                                                   correo.strip().lower())
                        if pwd:
                            return True
            except Exception:
                pass

        return False

    def get_bridge_status(self) -> dict:
        """Retorna el estado del bridge con el Sistema XML para mostrar en la UI."""
        if not _BRIDGE_AVAILABLE:
            return {
                "sistema_xml_encontrado": False,
                "mensaje": "Bridge no disponible — sistema_xml_bridge.py no encontrado.",
            }
        return get_bridge_status()

    def sync_folder_to_registro(self, email: str, carpeta: str):
        """
        Actualiza el campo 'carpeta' del cliente en el registro del Sistema XML
        y crea el archivo cliente.json si no existe.
        Se llama después de una descarga exitosa.
        """
        if not _BRIDGE_AVAILABLE:
            return
        try:
            clientes = load_registro()
            email_lower = email.strip().lower()
            for c in clientes:
                emails_lower = [e.strip().lower() for e in c.get("correos", [])]
                if email_lower in emails_lower:
                    c["carpeta"] = str(carpeta)
                    save_registro(clientes)
                    # Crear cliente.json si no existe — necesario para que
                    # el Sistema XML filtre correctamente por cédula del cliente
                    ensure_cliente_json(carpeta, email)
                    return
        except Exception as exc:
            warnings.warn(f"No se pudo actualizar carpeta en registro: {exc}",
                          stacklevel=3)

    # ------------------------------------------------------------------
    # Historial de descargas
    # ------------------------------------------------------------------

    def get_history(self) -> list:
        """Retorna la lista completa del historial de descargas."""
        return self.config.get("download_history", [])

    def add_history_entry(
        self,
        client_name: str,
        year: int,
        month: int,
        files_downloaded: int,
        files_skipped: int,
    ):
        """
        Registra una descarga completada.
        Si ya existe una entrada para el mismo cliente/año/mes, la actualiza
        en lugar de duplicarla.
        """
        from datetime import datetime as _dt

        history = self.config.setdefault("download_history", [])

        # Buscar entrada existente para actualizar
        for entry in history:
            if (entry.get("client") == client_name
                    and entry.get("year") == year
                    and entry.get("month") == month):
                entry["files"]    += files_downloaded
                entry["skipped"]  += files_skipped
                entry["last_run"]  = _dt.now().strftime("%Y-%m-%d %H:%M")
                entry["runs"]      = entry.get("runs", 1) + 1
                self.save()
                return

        # Nueva entrada
        history.append({
            "client":   client_name,
            "year":     year,
            "month":    month,
            "files":    files_downloaded,
            "skipped":  files_skipped,
            "last_run": _dt.now().strftime("%Y-%m-%d %H:%M"),
            "runs":     1,
        })
        self.save()

    def get_history_for(self, client_name: str, year: int, month: int) -> dict | None:
        """
        Retorna la entrada de historial para un cliente y período,
        o None si nunca se descargó.
        """
        for entry in self.get_history():
            if (entry.get("client") == client_name
                    and entry.get("year") == year
                    and entry.get("month") == month):
                return entry
        return None

    def clear_history(self):
        """Borra todo el historial de descargas."""
        self.config["download_history"] = []
        self.save()
