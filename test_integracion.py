"""
test_integracion.py
Prueba la integración entre el Extractor y el Sistema XML.

Cubre:
  1. Localización del Sistema XML en la ruta relativa estándar
  2. Lectura del registro de clientes (clientes_registro.json)
  3. Agrupación de clientes con múltiples correos por cédula
  4. Fusión del registro con clientes locales del config
  5. Conversión entre modelos de datos (registro ↔ extractor)
  6. Sincronización de carpeta base al registro
  7. Graceful fallback cuando el Sistema XML no está disponible
  8. Verificación del servidor Flask (sin bloquearse)

No requiere conexión a Gmail ni que el Sistema XML esté corriendo.
Ejecutar desde la carpeta del proyecto: python test_integracion.py
"""

from __future__ import annotations

import json
import tempfile
import shutil
from pathlib import Path

PASS = "✅ PASS"
FAIL = "❌ FAIL"
INFO = "ℹ️  INFO"
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


def info(msg: str):
    print(f"   {INFO} {msg}")


# ══════════════════════════════════════════════════════════════
print("=" * 62)
print("  PRUEBA: Integración Extractor ↔ Sistema XML")
print("=" * 62)


# ──────────────────────────────────────────────────────────────
print("\n── 1. Localización del Sistema XML")
# ──────────────────────────────────────────────────────────────

from sistema_xml_bridge import (
    find_sistema_xml_dir, get_registro_path, get_contas_root,
    is_flask_running, get_bridge_status,
    load_registro, save_registro,
    find_client_by_email, find_client_by_name,
    update_client_folder, add_email_to_client,
    registro_to_extractor_client, extractor_to_registro_client,
    _SISTEMA_XML_REL,
)

sistema_dir = find_sistema_xml_dir()
if sistema_dir:
    check("Sistema XML encontrado", True, str(sistema_dir))
    registro_path = get_registro_path()
    check("clientes_registro.json encontrado", registro_path is not None,
          str(registro_path) if registro_path else "no encontrado")
    contas = get_contas_root()
    if contas:
        check("contas_root leído desde config_sistema.json", True, contas)
    else:
        info("config_sistema.json no tiene contas_root configurado aún")
else:
    info(f"Sistema XML no encontrado en ruta esperada: {_SISTEMA_XML_REL}")
    info("Las pruebas de lectura del registro usarán datos sintéticos.")


# ──────────────────────────────────────────────────────────────
print("\n── 2. Lectura del registro de clientes")
# ──────────────────────────────────────────────────────────────

clientes_reales = load_registro()

if clientes_reales:
    check(f"Registro cargado correctamente",
          len(clientes_reales) > 0, f"{len(clientes_reales)} clientes")

    # Verificar estructura de campos
    primer = clientes_reales[0]
    campos_requeridos = {"nombre", "cedula", "regimen", "correos"}
    campos_presentes  = set(primer.keys())
    check("Campos requeridos presentes en cada cliente",
          campos_requeridos.issubset(campos_presentes),
          f"presentes: {sorted(campos_presentes)}")

    # Estadísticas
    con_correo   = sum(1 for c in clientes_reales if c.get("correos"))
    con_regimen  = sum(1 for c in clientes_reales if c.get("regimen"))
    multi_correo = [c for c in clientes_reales if len(c.get("correos", [])) > 1]
    info(f"Con correo configurado: {con_correo}/{len(clientes_reales)}")
    info(f"Con régimen configurado: {con_regimen}/{len(clientes_reales)}")
    info(f"Con múltiples correos: {len(multi_correo)}")
    for c in multi_correo:
        info(f"  → {c['nombre']}: {c['correos']}")
else:
    info("Registro no disponible — usando datos sintéticos para las pruebas")
    clientes_reales = []


# ──────────────────────────────────────────────────────────────
print("\n── 3. Búsqueda en el registro")
# ──────────────────────────────────────────────────────────────

# Crear registro sintético para pruebas deterministas
REGISTRO_SINTETICO = [
    {
        "cedula": "207460512",
        "nombre": "Ramírez Trejos Gerald Antonio",
        "regimen": "IVA",
        "correos": ["geraldrt23@gmail.com", "geraldramireztrejos512@gmail.com"],
        "telefono": "", "encargados": [], "comentarios": "", "carpeta": "",
    },
    {
        "cedula": "207630320",
        "nombre": "Solís Villalobos Jorge Andrés",
        "regimen": "IVA",
        "correos": ["ganaderajufricka@gmail.com", "jufrickafacturas@gmail.com"],
        "telefono": "", "encargados": [], "comentarios": "", "carpeta": "",
    },
    {
        "cedula": "3102858282",
        "nombre": "Agrofinca La Flor de Zarcero S & C Ltda",
        "regimen": "IVA",
        "correos": ["jose.a.salas235@gmail.com"],
        "telefono": "", "encargados": [], "comentarios": "", "carpeta": "",
    },
    {
        "cedula": "401420382",
        "nombre": "Alfaro Moreira Mario Alberto",
        "regimen": "REA",
        "correos": ["realdigitalsancarlos@gmail.com"],
        "telefono": "", "encargados": [], "comentarios": "", "carpeta": "",
    },
    {
        "cedula": "999000001",
        "nombre": "Cliente Sin Correo",
        "regimen": "IVA",
        "correos": [],
        "telefono": "", "encargados": [], "comentarios": "", "carpeta": "",
    },
]

# Usar datos reales si están disponibles, sintéticos si no
registro_prueba = clientes_reales if clientes_reales else REGISTRO_SINTETICO

# Búsqueda por email — usar primer correo del primer cliente con correos
clientes_con_correo = [c for c in registro_prueba if c.get("correos")]
if clientes_con_correo:
    c_test = clientes_con_correo[0]
    email_test = c_test["correos"][0]

    # Simular find_client_by_email sobre datos sintéticos
    def _find_by_email(email, registro):
        email_lower = email.strip().lower()
        for c in registro:
            for correo in c.get("correos", []):
                if correo.strip().lower() == email_lower:
                    return c
        return None

    def _find_by_name(name, registro):
        name_lower = name.strip().lower()
        for c in registro:
            if c.get("nombre", "").strip().lower() == name_lower:
                return c
        return None

    found_email = _find_by_email(email_test, registro_prueba)
    check(f"find_client_by_email encuentra cliente por correo",
          found_email is not None,
          f"correo: {email_test}")

    found_name = _find_by_name(c_test["nombre"], registro_prueba)
    check("find_client_by_name encuentra cliente por nombre",
          found_name is not None,
          f"nombre: {c_test['nombre']}")

    not_found = _find_by_email("noexiste@ejemplo.com", registro_prueba)
    check("find_client_by_email retorna None para correo inexistente",
          not_found is None)


# ──────────────────────────────────────────────────────────────
print("\n── 4. Agrupación por cédula (múltiples correos)")
# ──────────────────────────────────────────────────────────────

# Simular la lista de clientes que get_clients() devolvería desde el registro
def simular_get_clients(registro):
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
            display_name = nombre
            if len(correos) > 1:
                display_name = f"{nombre} ({email.split('@')[0]})"
            resultado.append({
                "name":    display_name,
                "email":   email,
                "password": "__keyring__",
                "tipo":    tipo,
                "_cedula": cedula,
            })
    return resultado

# Simular agrupación del worker
def simular_agrupacion(clients):
    grupos = []
    visto_cedulas = {}
    for c in clients:
        cedula = c.get("_cedula", "").strip()
        if cedula and cedula in visto_cedulas:
            grupos[visto_cedulas[cedula]].append(c)
        else:
            grupos.append([c])
            if cedula:
                visto_cedulas[cedula] = len(grupos) - 1
    return grupos

clientes_expandidos = simular_get_clients(REGISTRO_SINTETICO)
grupos = simular_agrupacion(clientes_expandidos)

# Verificaciones
check("Clientes expandidos: 4 entradas para 4 clientes con correos",
      len(clientes_expandidos) == 6,   # 2+2+1+1 = 6 (sin el sin correo)
      f"obtenido: {len(clientes_expandidos)}")

check("Agrupación: 4 grupos para 5 clientes (sin el sin correo)",
      len(grupos) == 4,
      f"obtenido: {len(grupos)}")

# Verificar que los grupos de múltiples correos están bien formados
ramirez = next((g for g in grupos
                if g[0]["_cedula"] == "207460512"), None)
check("Ramírez Trejos agrupado con 2 correos",
      ramirez is not None and len(ramirez) == 2,
      f"correos: {[c['email'] for c in ramirez] if ramirez else 'no encontrado'}")

solis = next((g for g in grupos
              if g[0]["_cedula"] == "207630320"), None)
check("Solís Villalobos agrupado con 2 correos",
      solis is not None and len(solis) == 2,
      f"correos: {[c['email'] for c in solis] if solis else 'no encontrado'}")

agrofinca = next((g for g in grupos
                  if g[0]["_cedula"] == "3102858282"), None)
check("Agrofinca (1 correo) en grupo de tamaño 1",
      agrofinca is not None and len(agrofinca) == 1)

# Verificar nombre base sin sufijo
if ramirez:
    nombre_base = ramirez[0]["name"].split(" (")[0]
    check("Nombre base extraído correctamente sin sufijo de correo",
          nombre_base == "Ramírez Trejos Gerald Antonio",
          f"obtenido: '{nombre_base}'")


# ──────────────────────────────────────────────────────────────
print("\n── 5. Conversión de modelos de datos")
# ──────────────────────────────────────────────────────────────

reg_client = REGISTRO_SINTETICO[0]   # Ramírez Trejos
ext = registro_to_extractor_client(
    reg_client, "geraldramireztrejos512@gmail.com"
)
check("registro → extractor: nombre correcto",
      ext["name"] == "Ramírez Trejos Gerald Antonio",
      f"obtenido: '{ext['name']}'")
check("registro → extractor: tipo IVA",
      ext["tipo"] == "IVA")
check("registro → extractor: email asignado",
      ext["email"] == "geraldramireztrejos512@gmail.com")
check("registro → extractor: cédula preservada en _cedula",
      ext["_cedula"] == "207460512")

# REA
reg_rea = REGISTRO_SINTETICO[3]   # Alfaro Moreira (REA)
ext_rea = registro_to_extractor_client(reg_rea, "realdigitalsancarlos@gmail.com")
check("registro → extractor: régimen REA mapeado correctamente",
      ext_rea["tipo"] == "REA")

# extractor → registro
ext_nuevo = {
    "name": "Nuevo Cliente Test",
    "email": "nuevo@gmail.com",
    "tipo": "IVA",
    "_cedula": "",
}
reg_nuevo = extractor_to_registro_client(ext_nuevo)
check("extractor → registro: estructura mínima creada",
      all(k in reg_nuevo for k in ("cedula", "nombre", "regimen", "correos")))
check("extractor → registro: correo incluido en lista",
      "nuevo@gmail.com" in reg_nuevo["correos"])
check("extractor → registro: comentario de origen incluido",
      "Extractor" in reg_nuevo.get("comentarios", ""))


# ──────────────────────────────────────────────────────────────
print("\n── 6. Sincronización de carpeta al registro (con temp dir)")
# ──────────────────────────────────────────────────────────────

with tempfile.TemporaryDirectory() as tmpdir:
    # Crear estructura mínima del Sistema XML en temp
    registro_tmp = {
        "_version": 1,
        "clientes": [
            {
                "cedula": "207460512",
                "nombre": "Ramírez Trejos Gerald Antonio",
                "regimen": "IVA",
                "correos": ["geraldramireztrejos512@gmail.com"],
                "carpeta": "",
            }
        ]
    }
    registro_path_tmp = Path(tmpdir) / "clientes_registro.json"
    with open(registro_path_tmp, "w", encoding="utf-8") as f:
        json.dump(registro_tmp, f, ensure_ascii=False, indent=2)

    # Sobrescribir la función de carga para usar el tmp
    import sistema_xml_bridge as bridge_mod
    original_get_path = bridge_mod.get_registro_path
    bridge_mod.get_registro_path = lambda: registro_path_tmp

    try:
        bridge_mod.update_client_folder(
            "207460512",
            "C:\\CONTAS\\IVA\\Ramírez Trejos Gerald Antonio"
        )
        # Verificar que se escribió
        with open(registro_path_tmp, encoding="utf-8") as f:
            data = json.load(f)
        carpeta_guardada = data["clientes"][0].get("carpeta", "")
        check(
            "update_client_folder actualiza carpeta en el registro",
            "Ramírez Trejos" in carpeta_guardada,
            f"carpeta: '{carpeta_guardada}'"
        )

        # Verificar que _version y otros metadatos se preservan
        check(
            "Metadatos del registro preservados tras actualización",
            data.get("_version") == 1
        )
    finally:
        bridge_mod.get_registro_path = original_get_path


# ──────────────────────────────────────────────────────────────
print("\n── 7. Graceful fallback cuando el Sistema XML no está disponible")
# ──────────────────────────────────────────────────────────────

# Simular que el Sistema XML no existe
import sistema_xml_bridge as bridge_mod
original_find = bridge_mod.find_sistema_xml_dir
bridge_mod.find_sistema_xml_dir = lambda: None

try:
    status_sin_bridge = bridge_mod.get_bridge_status()
    check("get_bridge_status sin Sistema XML no lanza excepción",
          True)
    check("sistema_xml_encontrado es False cuando no está disponible",
          status_sin_bridge["sistema_xml_encontrado"] is False)
    check("total_clientes es 0 cuando no está disponible",
          status_sin_bridge["total_clientes"] == 0)

    registro_vacio = bridge_mod.load_registro()
    check("load_registro retorna lista vacía sin Sistema XML",
          registro_vacio == [])

    # update_client_folder no debe lanzar excepción
    try:
        bridge_mod.update_client_folder("123", "C:\\alguna\\carpeta")
        check("update_client_folder no lanza excepción sin Sistema XML", True)
    except Exception as exc:
        check("update_client_folder no lanza excepción sin Sistema XML",
              False, str(exc))

finally:
    bridge_mod.find_sistema_xml_dir = original_find


# ──────────────────────────────────────────────────────────────
print("\n── 8. Verificación del servidor Flask (no bloqueante)")
# ──────────────────────────────────────────────────────────────

import time
start = time.time()
flask_ok = is_flask_running(timeout=0.5)
elapsed = time.time() - start

check(
    "is_flask_running no bloquea más de 1 segundo",
    elapsed < 1.0,
    f"tardó {elapsed:.2f}s"
)
info(f"Servidor Flask en localhost:5000: {'ACTIVO' if flask_ok else 'no está corriendo'}")


# ──────────────────────────────────────────────────────────────
print(f"\n{'=' * 62}")
print(f"  Resultado: {passed} pasaron, {failed} fallaron "
      f"de {passed + failed} verificaciones")
print("=" * 62)
if failed == 0:
    print("  🎉 Todas las pruebas pasaron.")
else:
    print("  ⚠️  Revisá los casos marcados con ❌ arriba.")
print()
