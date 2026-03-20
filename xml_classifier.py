"""
xml_classifier.py
Clasifica archivos XML de facturas electrónicas según su tipo de comprobante.
Compatible con el formato de Costa Rica (Ministerio de Hacienda / DGT).
"""

import xml.etree.ElementTree as ET


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
        root_local = self._local_name(root.tag)
        for key, folder in self.ROOT_TAG_MAP.items():
            if key.lower() in root_local.lower():
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

    @staticmethod
    def _local_name(tag: str) -> str:
        """Elimina el namespace de un tag XML: {namespace}LocalName → LocalName"""
        return tag.split("}")[-1] if "}" in tag else tag
