"""
test_keyring.py
Prueba el almacenamiento seguro de contraseñas con keyring.
Ejecutar desde la carpeta del proyecto: python test_keyring.py

Al finalizar, el script limpia todos los datos de prueba que creó.
"""

import json
from config_manager import ConfigManager, _KEYRING_AVAILABLE, _PWD_IN_KEYRING

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  AVISO"

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


print("=" * 60)
print("  PRUEBA: Gestión segura de contraseñas")
print("=" * 60)

# ──────────────────────────────────────────────────────────────
print(f"\n── 1. Disponibilidad de keyring")
# ──────────────────────────────────────────────────────────────
print(f"   {'ℹ️ ' } keyring disponible en este entorno: {_KEYRING_AVAILABLE}")
if not _KEYRING_AVAILABLE:
    print(f"   {WARN} keyring no instalado. Las pruebas de keyring se saltarán.")
    print(f"         Para instalarlo: pip install keyring")

# ──────────────────────────────────────────────────────────────
print(f"\n── 2. Agregar cliente y verificar almacenamiento")
# ──────────────────────────────────────────────────────────────
cfg = ConfigManager()
clientes_antes = len(cfg.get_clients())

TEST_EMAIL = "test_prueba_gestor@gmail.com"
TEST_PWD   = "abcdefghijklmnop"   # 16 chars válidos
TEST_NAME  = "_ClientePrueba_"

cfg.add_client(name=TEST_NAME, email=TEST_EMAIL, password=TEST_PWD, tipo="IVA")
clientes = cfg.get_clients()
cliente  = next((c for c in clientes if c["email"] == TEST_EMAIL), None)

check("Cliente fue agregado a la lista", cliente is not None)

if cliente:
    if _KEYRING_AVAILABLE:
        check(
            "Contraseña en JSON es el sentinel '__keyring__'",
            cliente["password"] == _PWD_IN_KEYRING,
            f"valor actual: '{cliente['password']}'"
        )
    else:
        check(
            "Contraseña en JSON es base64 (fallback sin keyring)",
            cliente["password"] != TEST_PWD and cliente["password"] != _PWD_IN_KEYRING,
            f"valor actual: '{cliente['password'][:20]}...'"
        )

# ──────────────────────────────────────────────────────────────
print(f"\n── 3. Recuperar contraseña correctamente")
# ──────────────────────────────────────────────────────────────
if cliente:
    try:
        pwd_recuperada = cfg.decode_password(cliente["password"], TEST_EMAIL)
        check(
            "Contraseña recuperada coincide con la original",
            pwd_recuperada == TEST_PWD,
            f"recuperada: '{pwd_recuperada}'"
        )
    except Exception as exc:
        check("decode_password no debe lanzar excepción", False, str(exc))

# ──────────────────────────────────────────────────────────────
print(f"\n── 4. Verificar que config.json NO contiene la contraseña en claro")
# ──────────────────────────────────────────────────────────────
try:
    config_path = ConfigManager.CONFIG_FILE
    with open(config_path, "r", encoding="utf-8") as f:
        raw_json = f.read()

    check(
        "Contraseña en texto claro NO está en config.json",
        TEST_PWD not in raw_json,
        "la contraseña real no debe aparecer en el archivo"
    )
    if _KEYRING_AVAILABLE:
        check(
            "Sentinel '__keyring__' aparece en config.json",
            _PWD_IN_KEYRING in raw_json
        )
except FileNotFoundError:
    print(f"   {WARN} No se encontró config.json — omitiendo verificación de archivo")

# ──────────────────────────────────────────────────────────────
print(f"\n── 5. Editar cliente (cambio de contraseña)")
# ──────────────────────────────────────────────────────────────
clientes = cfg.get_clients()
idx = next((i for i, c in enumerate(clientes) if c["email"] == TEST_EMAIL), None)

if idx is not None:
    NEW_PWD = "ponmlkjihgfedcba"   # 16 chars distintos
    cfg.update_client(idx, TEST_NAME, TEST_EMAIL, NEW_PWD, "IVA")

    cliente_actualizado = cfg.get_clients()[idx]
    try:
        pwd_nueva = cfg.decode_password(cliente_actualizado["password"], TEST_EMAIL)
        check(
            "Contraseña actualizada se recupera correctamente",
            pwd_nueva == NEW_PWD,
            f"recuperada: '{pwd_nueva}'"
        )
        check(
            "Contraseña anterior ya no es válida tras la actualización",
            pwd_nueva != TEST_PWD
        )
    except Exception as exc:
        check("decode_password tras update no debe lanzar excepción", False, str(exc))

# ──────────────────────────────────────────────────────────────
print(f"\n── 6. Prueba de migración base64 → keyring")
# ──────────────────────────────────────────────────────────────
if _KEYRING_AVAILABLE:
    import base64

    # Insertar manualmente un cliente con contraseña en base64 (como la versión anterior)
    LEGACY_EMAIL = "legacy_test_gestor@gmail.com"
    LEGACY_PWD   = "zyxwvutsrqponmlk"
    LEGACY_B64   = base64.b64encode(LEGACY_PWD.encode()).decode()

    cfg.config["clients"].append({
        "name":     "_LegacyTest_",
        "email":    LEGACY_EMAIL,
        "password": LEGACY_B64,
        "tipo":     "IVA",
    })
    cfg.save()

    # Recargar config — aquí debe ocurrir la migración automática
    cfg2 = ConfigManager()
    legacy = next((c for c in cfg2.get_clients() if c["email"] == LEGACY_EMAIL), None)

    if legacy:
        check(
            "Cliente legacy fue encontrado tras recarga",
            legacy is not None
        )
        check(
            "Contraseña legacy fue migrada al sentinel '__keyring__'",
            legacy["password"] == _PWD_IN_KEYRING,
            f"valor actual: '{legacy['password']}'"
        )
        try:
            pwd_migrada = cfg2.decode_password(legacy["password"], LEGACY_EMAIL)
            check(
                "Contraseña migrada se recupera correctamente desde keyring",
                pwd_migrada == LEGACY_PWD,
                f"recuperada: '{pwd_migrada}'"
            )
        except Exception as exc:
            check("decode_password de contraseña migrada no debe fallar", False, str(exc))

        # Limpiar cliente legacy
        idx_legacy = next(
            (i for i, c in enumerate(cfg2.get_clients()) if c["email"] == LEGACY_EMAIL), None
        )
        if idx_legacy is not None:
            cfg2.remove_client(idx_legacy)
    else:
        print(f"   {WARN} No se encontró cliente legacy tras recarga — omitiendo prueba de migración")
else:
    print(f"   ℹ️  keyring no disponible — prueba de migración omitida")

# ──────────────────────────────────────────────────────────────
print(f"\n── 7. Eliminar cliente de prueba y limpiar keyring")
# ──────────────────────────────────────────────────────────────
cfg_final = ConfigManager()
idx_final = next(
    (i for i, c in enumerate(cfg_final.get_clients()) if c["email"] == TEST_EMAIL), None
)
if idx_final is not None:
    cfg_final.remove_client(idx_final)
    check(
        "Cliente de prueba eliminado correctamente",
        all(c["email"] != TEST_EMAIL for c in cfg_final.get_clients())
    )
    if _KEYRING_AVAILABLE:
        # Verificar que la contraseña ya no existe en keyring
        try:
            import keyring as kr
            pwd_post = kr.get_password("GestorFacturas", TEST_EMAIL)
            check(
                "Contraseña eliminada del keyring al borrar cliente",
                pwd_post is None,
                f"valor en keyring: {pwd_post}"
            )
        except Exception:
            pass
else:
    print(f"   {WARN} Cliente de prueba no encontrado para limpiar (puede que ya fue eliminado)")

# ──────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print(f"  Resultado: {passed} pasaron, {failed} fallaron de {passed + failed} verificaciones")
print("=" * 60)
if failed == 0:
    print("  🎉 Todas las pruebas pasaron.")
else:
    print("  ⚠️  Revise los fallos indicados arriba.")
print()
