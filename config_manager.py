"""
config_manager.py
Gestión de configuración y clientes del Gestor de Facturas Electrónicas.
Almacena la configuración en un archivo JSON local.
"""

import json
import os
import base64
from pathlib import Path


class ConfigManager:
    """Maneja la configuración persistente de la aplicación."""

    CONFIG_DIR  = Path.home() / "AppData" / "Local" / "GestorFacturas"
    CONFIG_FILE = CONFIG_DIR / "config.json"

    DEFAULT_CONFIG = {
        "base_path_iva":  "",
        "base_path_rea":  "",
        "clients":        [],
        "default_folder": "CyG",
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
        return self.config.get("clients", [])

    def add_client(self, name: str, email: str, password: str, tipo: str = "IVA"):
        """Agrega un cliente. La contraseña se codifica en base64."""
        encoded_pwd = base64.b64encode(password.encode("utf-8")).decode("utf-8")
        self.config["clients"].append({
            "name":     name,
            "email":    email,
            "password": encoded_pwd,
            "tipo":     tipo,
        })
        self.save()

    def update_client(self, index: int, name: str, email: str,
                      password: str, tipo: str = "IVA"):
        """Actualiza los datos de un cliente existente."""
        encoded_pwd = base64.b64encode(password.encode("utf-8")).decode("utf-8")
        self.config["clients"][index] = {
            "name":     name,
            "email":    email,
            "password": encoded_pwd,
            "tipo":     tipo,
        }
        self.save()

    def remove_client(self, index: int):
        self.config["clients"].pop(index)
        self.save()

    def decode_password(self, encoded: str) -> str:
        """Devuelve la contraseña decodificada."""
        return base64.b64decode(encoded.encode("utf-8")).decode("utf-8")

    def email_exists(self, email: str) -> bool:
        return any(c["email"] == email.lower().strip()
                   for c in self.get_clients())

    def name_exists(self, name: str) -> bool:
        return any(c["name"].lower() == name.lower().strip()
                   for c in self.get_clients())
