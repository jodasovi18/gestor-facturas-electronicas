"""
xml_classifier.py
Clasifica archivos XML de facturas electrónicas según su tipo de comprobante.
Compatible con el formato de Costa Rica (Ministerio de Hacienda / DGT).
"""

import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional


class XMLClassifier:
    """
    Determina la subcarpeta de destino según el tipo de comprobante electrónico.
    Soporta el esquema de facturas electrónicas de Costa Rica y formatos genéricos.
    """

    # Mapeo de elemento raíz → nombre de subcarpeta
    ROOT_TAG_MAP = {
        "FacturaElectronica":              "Facturas",
        "FacturaElectronicaCompra":        "Facturas de Compra",
        "FacturaElectronicaExportacion":   "Facturas de Exportación",
        "TiqueteElectronico":              "Tiquetes",
        "NotaDebitoElectronica":           "Notas de Débito",
        "NotaCreditoElectronica":          "Notas de Crédito",
        "MensajeReceptor":                 "Mensajes Receptor",
        "MensajeHacienda":                 "Mensajes Hacienda",
    }

    # Mapeo de campo TipoDocumento numérico (otros países / formatos legacy)
    TIPO_DOCUMENTO_MAP = {
        "01": "Facturas",
        "02": "Notas de Débito",
        "03": "Notas de Crédito",
        "04": "Tiquetes",
        "08": "Facturas de Compra",
        "09": "Facturas de Exportación",
    }

    def __init__(self, default_folder: str = "CyG"):
        self.default_folder = default_folder

    def classify(self, xml_data: bytes) -> str:
        """
        Analiza el XML y retorna el nombre de la subcarpeta apropiada.
        Si no puede determinar el tipo, devuelve `default_folder`.
        """
        try:
            xml_text = xml_data.decode("utf-8", errors="ignore") if isinstance(xml_data, bytes) else xml_data
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return self.default_folder

        # 1. Intentar por elemento raíz (formato Costa Rica)
        # Primero match exacto, luego substring — evita que "FacturaElectronica"
        # absorba "FacturaElectronicaCompra" o "FacturaElectronicaExportacion".
        root_local = self._local_name(root.tag)
        root_lower = root_local.lower()

        # Paso 1a: coincidencia exacta
        for key, folder in self.ROOT_TAG_MAP.items():
            if key.lower() == root_lower:
                return folder

        # Paso 1b: coincidencia por substring (compatibilidad con variantes)
        for key, folder in self.ROOT_TAG_MAP.items():
            if key.lower() in root_lower:
                return folder

        # 2. Buscar campo TipoDocumento dentro del XML
        for elem in root.iter():
            local = self._local_name(elem.tag)
            if local in ("TipoDocumento", "tipo_documento", "CodigoTipoComprobante"):
                tipo = (elem.text or "").strip()
                if tipo in self.TIPO_DOCUMENTO_MAP:
                    return self.TIPO_DOCUMENTO_MAP[tipo]

        # 3. Fallback
        return self.default_folder

    def get_document_type_label(self, xml_data: bytes) -> str:
        """Devuelve la etiqueta legible del tipo de comprobante."""
        folder = self.classify(xml_data)
        return folder if folder != self.default_folder else "Sin clasificar"

    def extract_date(self, xml_data: bytes) -> Optional[datetime]:
        """
        Extrae la fecha de emisión del comprobante electrónico.

        Busca los campos estándar del esquema de Hacienda (Costa Rica):
          - FechaEmision         → formato ISO 8601: 2024-03-15T10:30:00-06:00
          - FechaEmisionDoc      → variante legacy
          - FechaEmision (attr)  → a veces viene como atributo del nodo raíz

        Retorna un objeto datetime (sin zona horaria) o None si no se puede leer.
        """
        try:
            xml_text = (
                xml_data.decode("utf-8", errors="ignore")
                if isinstance(xml_data, bytes)
                else xml_data
            )
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None

        # Campos candidatos en orden de prioridad
        _DATE_FIELDS = ("FechaEmision", "FechaEmisionDoc", "Fecha")

        for elem in root.iter():
            local = self._local_name(elem.tag)
            if local in _DATE_FIELDS and elem.text:
                date_str = elem.text.strip()
                parsed = self._parse_iso_date(date_str)
                if parsed:
                    return parsed

        # Intentar también como atributo del nodo raíz
        for attr_name in _DATE_FIELDS:
            if attr_name in root.attrib:
                parsed = self._parse_iso_date(root.attrib[attr_name])
                if parsed:
                    return parsed

        return None

    @staticmethod
    def _parse_iso_date(date_str: str) -> Optional[datetime]:
        """
        Parsea una fecha ISO 8601 con o sin zona horaria.
        Formatos soportados:
          - 2024-03-15T10:30:00-06:00
          - 2024-03-15T10:30:00Z
          - 2024-03-15T10:30:00
          - 2024-03-15
        Retorna datetime naive (sin tzinfo) para simplificar comparaciones.
        """
        # Quitar zona horaria (±HH:MM o Z) para un parse simple
        clean = date_str.strip()
        # Reemplazar Z por offset vacío
        clean = clean.replace("Z", "")
        # Quitar offset ±HH:MM al final si existe
        if len(clean) > 19 and clean[19] in ("+", "-"):
            clean = clean[:19]
        # Quitar microsegundos/milisegundos si existen (ej: 12:00:00.000)
        if len(clean) > 19 and clean[19] == ".":
            clean = clean[:19]

        formats = (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d",
        )
        for fmt in formats:
            try:
                return datetime.strptime(clean, fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _local_name(tag: str) -> str:
        """Elimina el namespace de un tag XML: {namespace}LocalName → LocalName"""
        return tag.split("}")[-1] if "}" in tag else tag
