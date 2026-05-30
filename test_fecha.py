"""
test_fecha.py
Prueba la extracción de fecha de emisión desde XMLs de comprobantes electrónicos.
Ejecutar desde la carpeta del proyecto: python test_fecha.py
"""

from xml_classifier import XMLClassifier

classifier = XMLClassifier()

# ──────────────────────────────────────────────
# XMLs de prueba
# ──────────────────────────────────────────────

# 1. Factura con fecha completa ISO 8601 + zona horaria (formato Hacienda CR)
xml_factura_enero = b"""<?xml version="1.0" encoding="UTF-8"?>
<FacturaElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <Clave>50601012400310287489900100001010000000011199999999</Clave>
  <FechaEmision>2024-01-28T10:30:00-06:00</FechaEmision>
  <Emisor><Nombre>Empresa de Prueba SA</Nombre></Emisor>
</FacturaElectronica>"""

# 2. Tiquete con fecha sin zona horaria
xml_tiquete_marzo = b"""<?xml version="1.0" encoding="UTF-8"?>
<TiqueteElectronico xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <Clave>50604032400310287489900100001010000000011199999999</Clave>
  <FechaEmision>2024-03-05T08:15:00</FechaEmision>
  <Emisor><Nombre>Tienda de Prueba</Nombre></Emisor>
</TiqueteElectronico>"""

# 3. Nota de crédito con fecha solo en formato YYYY-MM-DD (sin hora)
xml_nota_credito = b"""<?xml version="1.0" encoding="UTF-8"?>
<NotaCreditoElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <Clave>50613062400310287489900100001010000000011199999999</Clave>
  <FechaEmision>2024-06-13</FechaEmision>
</NotaCreditoElectronica>"""

# 4. XML sin campo FechaEmision (debe retornar None → fallback al mes del correo)
xml_sin_fecha = b"""<?xml version="1.0" encoding="UTF-8"?>
<FacturaElectronica xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <Clave>506010124003</Clave>
  <Emisor><Nombre>Sin Fecha SA</Nombre></Emisor>
</FacturaElectronica>"""

# 5. XML malformado (debe retornar None sin lanzar excepción)
xml_corrupto = b"<esto no es xml valido <<<"

# 6. MensajeReceptor con fecha en formato Z (UTC)
xml_mensaje = b"""<?xml version="1.0" encoding="UTF-8"?>
<MensajeReceptor xmlns="https://cdn.comprobanteselectronicos.go.cr/xml/v4.3">
  <FechaEmisionDoc>2024-11-30T23:59:00Z</FechaEmisionDoc>
</MensajeReceptor>"""


# ──────────────────────────────────────────────
# Ejecución de pruebas
# ──────────────────────────────────────────────

PASS = "✅ PASS"
FAIL = "❌ FAIL"

cases = [
    {
        "nombre":        "Factura enero — ISO con zona horaria (-06:00)",
        "xml":           xml_factura_enero,
        "expect_year":   2024,
        "expect_month":  1,
        "expect_none":   False,
        "expect_type":   "Facturas",
    },
    {
        "nombre":        "Tiquete marzo — ISO sin zona horaria",
        "xml":           xml_tiquete_marzo,
        "expect_year":   2024,
        "expect_month":  3,
        "expect_none":   False,
        "expect_type":   "Tiquetes",
    },
    {
        "nombre":        "Nota de Crédito junio — solo fecha YYYY-MM-DD",
        "xml":           xml_nota_credito,
        "expect_year":   2024,
        "expect_month":  6,
        "expect_none":   False,
        "expect_type":   "Notas de Crédito",
    },
    {
        "nombre":        "XML sin FechaEmision — debe retornar None",
        "xml":           xml_sin_fecha,
        "expect_year":   None,
        "expect_month":  None,
        "expect_none":   True,
        "expect_type":   "Facturas",
    },
    {
        "nombre":        "XML corrupto — debe retornar None sin excepción",
        "xml":           xml_corrupto,
        "expect_year":   None,
        "expect_month":  None,
        "expect_none":   True,
        "expect_type":   "CyG",   # fallback default
    },
    {
        "nombre":        "MensajeReceptor noviembre — ISO con Z (UTC)",
        "xml":           xml_mensaje,
        "expect_year":   2024,
        "expect_month":  11,
        "expect_none":   False,
        "expect_type":   "Mensajes Receptor",
    },
]

print("=" * 60)
print("  PRUEBA: Extracción de fecha de comprobantes XML")
print("=" * 60)

passed = 0
failed = 0

for c in cases:
    print(f"\n── {c['nombre']}")

    # --- Fecha ---
    try:
        fecha = classifier.extract_date(c["xml"])
    except Exception as exc:
        print(f"   {FAIL} extract_date lanzó excepción inesperada: {exc}")
        failed += 1
        continue

    if c["expect_none"]:
        if fecha is None:
            print(f"   {PASS} extract_date → None  (correcto, sin fecha en el XML)")
            passed += 1
        else:
            print(f"   {FAIL} extract_date → {fecha}  (se esperaba None)")
            failed += 1
    else:
        if fecha is None:
            print(f"   {FAIL} extract_date → None  (se esperaba {c['expect_year']}-{c['expect_month']:02d})")
            failed += 1
        elif fecha.year == c["expect_year"] and fecha.month == c["expect_month"]:
            print(f"   {PASS} extract_date → {fecha}  (año={fecha.year}, mes={fecha.month})")
            passed += 1
        else:
            print(f"   {FAIL} extract_date → {fecha}  "
                  f"(se esperaba año={c['expect_year']}, mes={c['expect_month']})")
            failed += 1

    # --- Tipo de comprobante ---
    try:
        doc_type = classifier.classify(c["xml"])
    except Exception as exc:
        print(f"   {FAIL} classify lanzó excepción inesperada: {exc}")
        failed += 1
        continue

    if doc_type == c["expect_type"]:
        print(f"   {PASS} classify → '{doc_type}'")
        passed += 1
    else:
        print(f"   {FAIL} classify → '{doc_type}'  (se esperaba '{c['expect_type']}')")
        failed += 1

print("\n" + "=" * 60)
print(f"  Resultado: {passed} pasaron, {failed} fallaron de {passed + failed} verificaciones")
print("=" * 60)
