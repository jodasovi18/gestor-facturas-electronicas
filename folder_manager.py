"""
folder_manager.py
Gestiona la creación de la estructura de carpetas para cada cliente y período.

Estructura generada:
    [base_path] / [cliente] / [año] / [#-Mes] / [tipo_comprobante] /
"""

import os
from pathlib import Path


class FolderManager:
    """Crea y gestiona la estructura de carpetas mensual por cliente."""

    MONTHS_ES = {
        1:  "Ene",  2:  "Feb",  3:  "Mar",
        4:  "Abr",  5:  "May",  6:  "Jun",
        7:  "Jul",  8:  "Ago",  9:  "Set",
        10: "Oct",  11: "Nov",  12: "Dic",
    }

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)

    # ------------------------------------------------------------------
    # Rutas
    # ------------------------------------------------------------------

    def month_label(self, month: int) -> str:
        """Devuelve la etiqueta de mes en formato '2-Feb'."""
        return f"{month}-{self.MONTHS_ES[month]}"

    def get_client_month_path(self, client_name: str, year: int, month: int) -> Path:
        """Retorna la ruta del mes para un cliente (sin crear carpetas)."""
        return self.base_path / client_name / str(year) / self.month_label(month)

    def get_subfolder_path(
        self, client_name: str, year: int, month: int, subfolder: str
    ) -> Path:
        """Retorna la ruta completa incluyendo la subcarpeta de tipo de comprobante."""
        return self.get_client_month_path(client_name, year, month) / subfolder

    # ------------------------------------------------------------------
    # Creación
    # ------------------------------------------------------------------

    def ensure_subfolder(
        self, client_name: str, year: int, month: int, subfolder: str
    ) -> Path:
        """Crea la subcarpeta si no existe y la retorna."""
        path = self.get_subfolder_path(client_name, year, month, subfolder)
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ------------------------------------------------------------------
    # Nombres de archivo únicos
    # ------------------------------------------------------------------

    @staticmethod
    def unique_filepath(directory: Path, filename: str) -> Path:
        """
        Si el archivo ya existe, agrega un sufijo numérico para evitar sobreescritura.
        Ejemplo: factura.xml → factura_1.xml → factura_2.xml
        """
        filepath = directory / filename
        if not filepath.exists():
            return filepath

        stem = filepath.stem
        suffix = filepath.suffix
        counter = 1
        while filepath.exists():
            filepath = directory / f"{stem}_{counter}{suffix}"
            counter += 1
        return filepath

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    def get_existing_filenames(self, client_name: str, year: int, month: int) -> set:
        """
        Retorna un set con los nombres de archivo (en minúsculas) ya presentes
        en todas las subcarpetas del mes. Se usa para evitar re-descargar archivos.
        """
        month_path = self.get_client_month_path(client_name, year, month)
        filenames = set()
        if month_path.exists():
            for f in month_path.rglob("*"):
                if f.is_file():
                    filenames.add(f.name.lower())
        return filenames

    def preview(self, client_name: str, year: int, month: int) -> str:
        """Retorna una representación legible de la ruta del mes."""
        return str(self.get_client_month_path(client_name, year, month))

    def base_exists(self) -> bool:
        """Verifica si la carpeta base existe."""
        return self.base_path.exists() and self.base_path.is_dir()
