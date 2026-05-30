"""
test_xml_estructura.py
Prueba el clasificador y extractor de fecha contra XMLs con estructura inusual.

Cubre:
  - Versiones del esquema Hacienda CR: v4.1, v4.2, v4.3
  - Encodings: UTF-8, ISO-8859-1, UTF-8 con BOM
  - Namespaces distintos o ausentes
  - MensajeReceptor sin FechaEmision
  - Campos TipoDocumento numérico (formato legacy)
  - Fechas con microsegundos, con +00:00, con offset distinto a -06:00
  - XML con espacios en blanco o saltos de línea en la fecha
  - XML bien formado pero con tipo desconocido → fallback CyG
  - XML vacío o solo con declaración
  - Encoding ISO-8859-1 con caracteres latinos (ñ, tildes)

Ejecutar desde la carpeta del proyecto: python test_xml_estructura.py
"""

from xml_classifier import XMLClassifier

classifier = XMLClassifier(default_folder="CyG")

PASS = "✅ PASS"
FAIL = "❌ FAIL"

passed = 0
failed = 0


def check(label: str, got, expected, detail: str = ""):
    global passed, failed
    ok = got == expected
    estado = PASS if ok else FAIL
    suffix = f" — {detail}" if detail else ""
    if ok:
        print(f"   {estado} {label}{suffix}")
        passed += 1
    else:
        print(f"   {FAIL} {label}")
        print(f"         esperado:  {repr(expected)}")
        print(f"         obtenido:  {repr(got)}")
        failed += 1


def check_date(label: str, xml_data: bytes, exp_year, exp_month, exp_none=False):
    global passed, failed
    fecha = classifier.extract_date(xml_data)
    if exp_none:
        ok = fecha is None
        estado = PASS if ok else FAIL
        print(f"   {estado} {label} → {'None ✓' if ok else repr(fecha)}")
        passed += 1 if ok else 0
        failed += 0 if ok else 1
    else:
        ok = fecha is not None and fecha.year == exp_year and fecha.month == exp_month
        estado = PASS if ok else FAIL
        got_str = f"{fecha.year}-{fecha.month:02d}" if fecha else "None"
        print(f"   {estado} {label} → {got_str}" +
              (f" (esperado {exp_year}-{exp_month:02d})" if not ok else ""))
        passed += 1 if ok else 0
        failed += 0 if ok else 1


# ══════════════════════════════════════════════════════════════
print("=" * 62)
print("  PRUEBA: Clasificación y fecha en XMLs con estructura inusual")
print("=" * 62)


# ──────────────────────────────────────────────────────────────
print("\n── BLOQUE 1: Versiones del esquema Hacienda CR")
# ──────────────────────────────────────────────────────────────

# v4.1 — namespace antiguo
xml_v41 = b"""<?xml version="1.0" encoding="UTF-8"?>
<FacturaElectronica xmlns="https://tribunet.hacienda.go.cr/docs/esquemas/2017/v4.1/facturaElectronica">
  <FechaEmision>2021-06-10T08:00:00-06:00</FechaEmision>
</FacturaElectronica>"""

# v4.2
xml_v42 = b"""<?xml version="1.0" encoding="UTF-8"?>
<FacturaElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.2/facturaElectronica">
  <FechaEmision>2022-11-15T14:30:00-06:00</FechaEmision>
</FacturaElectronica>"""

# v4.3 (actual)
xml_v43 = b"""<?xml version="1.0" encoding="UTF-8"?>
<FacturaElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3/facturaElectronica">
  <FechaEmision>2024-03-20T09:15:00-06:00</FechaEmision>
</FacturaElectronica>"""

check("v4.1 — FacturaElectronica clasifica como Facturas",
      classifier.classify(xml_v41), "Facturas")
check_date("v4.1 — FechaEmision extraída", xml_v41, 2021, 6)

check("v4.2 — FacturaElectronica clasifica como Facturas",
      classifier.classify(xml_v42), "Facturas")
check_date("v4.2 — FechaEmision extraída", xml_v42, 2022, 11)

check("v4.3 — FacturaElectronica clasifica como Facturas",
      classifier.classify(xml_v43), "Facturas")
check_date("v4.3 — FechaEmision extraída", xml_v43, 2024, 3)


# ──────────────────────────────────────────────────────────────
print("\n── BLOQUE 2: Todos los tipos de comprobante (v4.3)")
# ──────────────────────────────────────────────────────────────

NS = 'xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3"'
FECHA = "<FechaEmision>2024-05-01T10:00:00-06:00</FechaEmision>"

tipos = [
    ("FacturaElectronica",            "Facturas"),
    ("FacturaElectronicaCompra",      "Facturas de Compra"),
    ("FacturaElectronicaExportacion", "Facturas de Exportación"),
    ("TiqueteElectronico",            "Tiquetes"),
    ("NotaDebitoElectronica",         "Notas de Débito"),
    ("NotaCreditoElectronica",        "Notas de Crédito"),
    ("MensajeReceptor",               "Mensajes Receptor"),
    ("MensajeHacienda",               "Mensajes Hacienda"),
]

for tag, expected_folder in tipos:
    xml = f'<?xml version="1.0"?><{tag} {NS}>{FECHA}</{tag}>'.encode()
    check(f"{tag} → {expected_folder}",
          classifier.classify(xml), expected_folder)


# ──────────────────────────────────────────────────────────────
print("\n── BLOQUE 3: Encodings")
# ──────────────────────────────────────────────────────────────

# ISO-8859-1 con caracteres latinos (ñ, tildes) — común en algunos proveedores
xml_latin1 = (
    '<?xml version="1.0" encoding="ISO-8859-1"?>'
    '<FacturaElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">'
    '<FechaEmision>2024-07-22T11:00:00-06:00</FechaEmision>'
    '<Emisor><Nombre>Distribuidora Rodríguez y Cía</Nombre></Emisor>'
    '</FacturaElectronica>'
).encode("iso-8859-1")

# UTF-8 con BOM (algunos sistemas Windows agregan BOM al guardar)
xml_utf8_bom = (
    b'\xef\xbb\xbf'   # BOM
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<TiqueteElectronico xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">'
    b'<FechaEmision>2024-08-10T07:30:00-06:00</FechaEmision>'
    b'</TiqueteElectronico>'
)

# UTF-16 — raro pero posible
xml_utf16 = (
    '<?xml version="1.0" encoding="UTF-16"?>'
    '<FacturaElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">'
    '<FechaEmision>2024-09-05T15:00:00-06:00</FechaEmision>'
    '</FacturaElectronica>'
).encode("utf-16")

check("ISO-8859-1 con caracteres latinos — clasifica correctamente",
      classifier.classify(xml_latin1), "Facturas")
check_date("ISO-8859-1 — FechaEmision extraída", xml_latin1, 2024, 7)

check("UTF-8 con BOM — clasifica correctamente",
      classifier.classify(xml_utf8_bom), "Tiquetes")
check_date("UTF-8 con BOM — FechaEmision extraída", xml_utf8_bom, 2024, 8)

# UTF-16: puede fallar el parse — esperamos fallback sin excepción
try:
    result_utf16 = classifier.classify(xml_utf16)
    fecha_utf16  = classifier.extract_date(xml_utf16)
    print(f"   ℹ️  UTF-16 classify → '{result_utf16}' (sin excepción ✓)")
    print(f"   ℹ️  UTF-16 extract_date → {fecha_utf16} (sin excepción ✓)")
    passed += 2
except Exception as exc:
    print(f"   {FAIL} UTF-16 lanzó excepción inesperada: {exc}")
    failed += 2


# ──────────────────────────────────────────────────────────────
print("\n── BLOQUE 4: Variantes de formato de fecha")
# ──────────────────────────────────────────────────────────────

def xml_with_date(date_str: str, tag: str = "FechaEmision") -> bytes:
    return (
        f'<?xml version="1.0"?>'
        f'<FacturaElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">'
        f'<{tag}>{date_str}</{tag}>'
        f'</FacturaElectronica>'
    ).encode()

# Offset +00:00 (UTC)
check_date("Fecha con offset +00:00",
           xml_with_date("2024-01-15T06:00:00+00:00"), 2024, 1)

# Offset -05:00 (otro país)
check_date("Fecha con offset -05:00",
           xml_with_date("2024-02-20T10:00:00-05:00"), 2024, 2)

# Con microsegundos (algunos sistemas generan esto)
check_date("Fecha con microsegundos (2024-04-01T12:00:00.000-06:00)",
           xml_with_date("2024-04-01T12:00:00.000-06:00"), 2024, 4)

# Espacios en blanco alrededor de la fecha
check_date("Fecha con espacios en blanco alrededor",
           xml_with_date("  2024-05-10T09:00:00-06:00  "), 2024, 5)

# Fecha con salto de línea (algunos generadores de XML hacen esto)
check_date("Fecha con salto de línea",
           xml_with_date("\n  2024-06-01T08:00:00-06:00\n"), 2024, 6)

# Solo fecha sin hora
check_date("Solo fecha YYYY-MM-DD",
           xml_with_date("2024-07-15"), 2024, 7)

# Fecha en campo FechaEmisionDoc (variante legacy)
check_date("Fecha en campo FechaEmisionDoc",
           xml_with_date("2024-08-20T10:00:00-06:00", "FechaEmisionDoc"), 2024, 8)

# Fecha claramente inválida → None
check_date("Fecha inválida 'no-es-una-fecha' → None",
           xml_with_date("no-es-una-fecha"), None, None, exp_none=True)

# Campo de fecha vacío → None
check_date("Campo FechaEmision vacío → None",
           xml_with_date(""), None, None, exp_none=True)


# ──────────────────────────────────────────────────────────────
print("\n── BLOQUE 5: MensajeReceptor — caso especial")
# ──────────────────────────────────────────────────────────────

# MensajeReceptor sin FechaEmision (usa FechaEmisionDoc)
xml_mr_con_doc = b"""<?xml version="1.0" encoding="UTF-8"?>
<MensajeReceptor xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <FechaEmisionDoc>2024-10-05T14:00:00-06:00</FechaEmisionDoc>
  <Mensaje>1</Mensaje>
</MensajeReceptor>"""

# MensajeReceptor completamente sin fecha → None es correcto
xml_mr_sin_fecha = b"""<?xml version="1.0" encoding="UTF-8"?>
<MensajeReceptor xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <Mensaje>1</Mensaje>
  <DetalleMensaje>Comprobante recibido</DetalleMensaje>
</MensajeReceptor>"""

check("MensajeReceptor clasifica como 'Mensajes Receptor'",
      classifier.classify(xml_mr_con_doc), "Mensajes Receptor")
check_date("MensajeReceptor con FechaEmisionDoc",
           xml_mr_con_doc, 2024, 10)

check("MensajeReceptor sin fecha clasifica correctamente igual",
      classifier.classify(xml_mr_sin_fecha), "Mensajes Receptor")
check_date("MensajeReceptor sin ninguna fecha → None (usa fecha del correo)",
           xml_mr_sin_fecha, None, None, exp_none=True)


# ──────────────────────────────────────────────────────────────
print("\n── BLOQUE 6: Formato TipoDocumento numérico (legacy)")
# ──────────────────────────────────────────────────────────────

def xml_tipo_numerico(codigo: str) -> bytes:
    return (
        f'<?xml version="1.0"?>'
        f'<ComprobanteElectronico>'
        f'<TipoDocumento>{codigo}</TipoDocumento>'
        f'<FechaEmision>2023-03-10T10:00:00-06:00</FechaEmision>'
        f'</ComprobanteElectronico>'
    ).encode()

codigos = [
    ("01", "Facturas"),
    ("02", "Notas de Débito"),
    ("03", "Notas de Crédito"),
    ("04", "Tiquetes"),
    ("08", "Facturas de Compra"),
    ("09", "Facturas de Exportación"),
]

for codigo, expected in codigos:
    check(f"TipoDocumento '{codigo}' → {expected}",
          classifier.classify(xml_tipo_numerico(codigo)), expected)

# Código desconocido → fallback
check("TipoDocumento desconocido '99' → CyG (fallback)",
      classifier.classify(xml_tipo_numerico("99")), "CyG")


# ──────────────────────────────────────────────────────────────
print("\n── BLOQUE 7: XMLs sin namespace")
# ──────────────────────────────────────────────────────────────

# Sin namespace — el _local_name igual debe funcionar
xml_sin_ns = b"""<?xml version="1.0" encoding="UTF-8"?>
<FacturaElectronica>
  <FechaEmision>2024-11-01T09:00:00-06:00</FechaEmision>
</FacturaElectronica>"""

xml_nota_sin_ns = b"""<?xml version="1.0" encoding="UTF-8"?>
<NotaCreditoElectronica>
  <FechaEmision>2024-12-15T16:00:00-06:00</FechaEmision>
</NotaCreditoElectronica>"""

check("Sin namespace — FacturaElectronica clasifica como Facturas",
      classifier.classify(xml_sin_ns), "Facturas")
check_date("Sin namespace — FechaEmision extraída", xml_sin_ns, 2024, 11)

check("Sin namespace — NotaCreditoElectronica clasifica correctamente",
      classifier.classify(xml_nota_sin_ns), "Notas de Crédito")
check_date("Sin namespace — fecha de Nota de Crédito", xml_nota_sin_ns, 2024, 12)


# ──────────────────────────────────────────────────────────────
print("\n── BLOQUE 8: XMLs problemáticos — robustez")
# ──────────────────────────────────────────────────────────────

# XML completamente vacío
check("XML vacío (b'') → fallback sin excepción",
      classifier.classify(b""), "CyG")
check_date("XML vacío → None sin excepción", b"", None, None, exp_none=True)

# Solo declaración XML, sin elemento raíz
xml_solo_declaracion = b'<?xml version="1.0" encoding="UTF-8"?>'
check("Solo declaración XML → fallback sin excepción",
      classifier.classify(xml_solo_declaracion), "CyG")
check_date("Solo declaración → None sin excepción",
           xml_solo_declaracion, None, None, exp_none=True)

# XML bien formado pero tipo desconocido
xml_desconocido = b"""<?xml version="1.0"?>
<DocumentoGenerico>
  <Fecha>2024-04-10</Fecha>
  <Monto>15000</Monto>
</DocumentoGenerico>"""
check("Tipo desconocido → fallback CyG",
      classifier.classify(xml_desconocido), "CyG")

# XML con elemento raíz vacío
xml_raiz_vacia = b"<FacturaElectronica/>"
check("Elemento raíz vacío sin fecha — clasifica correctamente",
      classifier.classify(xml_raiz_vacia), "Facturas")
check_date("Elemento raíz vacío — fecha → None",
           xml_raiz_vacia, None, None, exp_none=True)

# XML con caracteres especiales en el contenido (no en estructura)
xml_caracteres = b"""<?xml version="1.0" encoding="UTF-8"?>
<FacturaElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <FechaEmision>2024-03-01T10:00:00-06:00</FechaEmision>
  <Emisor><Nombre>Pa\xc3\xb1amet\xc3\xa9rica S.A.</Nombre></Emisor>
  <Detalle>Venta de art\xc3\xadculos varios &amp; servicios</Detalle>
</FacturaElectronica>"""
check("Caracteres especiales UTF-8 en contenido — clasifica correctamente",
      classifier.classify(xml_caracteres), "Facturas")
check_date("Caracteres especiales — fecha extraída", xml_caracteres, 2024, 3)

# XML con FechaEmision en atributo del nodo raíz (variante poco común)
xml_fecha_atributo = b"""<?xml version="1.0"?>
<FacturaElectronica FechaEmision="2024-09-15T12:00:00-06:00"
  xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <Clave>506</Clave>
</FacturaElectronica>"""
check("FechaEmision como atributo del nodo raíz — clasifica",
      classifier.classify(xml_fecha_atributo), "Facturas")
check_date("FechaEmision como atributo — fecha extraída",
           xml_fecha_atributo, 2024, 9)


# ──────────────────────────────────────────────────────────────
print(f"\n{'=' * 62}")
print(f"  Resultado: {passed} pasaron, {failed} fallaron "
      f"de {passed + failed} verificaciones")
print("=" * 62)
if failed == 0:
    print("  🎉 Todos los casos pasaron.")
else:
    print("  ⚠️  Revisá los casos marcados con ❌ arriba.")
    print("      Pueden requerir mejoras en xml_classifier.py.")
print()
