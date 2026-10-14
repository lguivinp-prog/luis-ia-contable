"""Chromium REAL + frontend REAL + backend REAL con datos SINTETICOS: registro de un perfil de credencial SOL PRINCIPAL junto a una secundaria.

Requisitos (los levanta `m2-live/cred_fixture_up.ps1`): BD propia `m2credfix` (NO la real), backend de fixtures en :8012 sin broker ni agente, frontend en :3010.
Las llamadas /api/v1 del navegador se reenvian al backend de fixtures (nada llega al backend real ni a SUNAT). Todas las credenciales son FICTICIAS.
Verifica: validacion previa del motivo largo (la causa de la incidencia), alta unica por doble clic, coexistencia con la secundaria byte a byte, lista tras
refrescar, fallo ambiguo (500 DESPUES de persistir -> no se reenvia), Vault bloqueada, ausencia de secretos en respuestas/DOM y de llamadas a sol-access."""
from __future__ import annotations

import json
import re
import subprocess
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3010"
FIX = "http://127.0.0.1:8012"
ADMIN, PASSWORD = "admin@fixture-ficticio.com", "Fixture-Only-Pass-123!"
VAULT_PASSWORD = "Boveda-Ficticia-Solo-Pruebas-77!"
SEC_USER, SEC_PASS = "SECUNDARIAUSR1", "Secundaria-Clave-Ficticia-1"
PRI_USER, PRI_PASS = "PRINCIPALUSR2", "Principal-Clave-Ficticia-2"
TWO_USER, TWO_PASS = "SEGUNDAUSR3", "Segunda-Clave-Ficticia-3"
LONG_BASIS = "Acceso autorizado por el titular para consultas y pruebas supervisadas de destinos, sin presentacion ni pago de ningun tipo (ficticio)."
SECRETS = (SEC_USER, SEC_PASS, PRI_USER, PRI_PASS, TWO_USER, TWO_PASS, VAULT_PASSWORD, "BLOQUEADAUSR5", "Bloqueada-Clave-Ficticia-5", "OTROUSR4", "Otra-Clave-Ficticia-4")


def q(sql: str) -> str:
    return subprocess.run(["docker", "exec", "m2-native-pg", "psql", "-U", "m2native", "-d", "m2credfix", "-At", "-c", sql], capture_output=True, text=True).stdout.strip()


CID = q("select id from clients limit 1")
results: dict[str, object] = {}
mutations: list[str] = []
bodies: list[str] = []
persist_then_500 = {"armed": False, "fired": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    results[name] = "PASS" if ok else f"FAIL {detail}"
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail and not ok else ""), flush=True)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1100, "height": 900})
    page = ctx.new_page()

    def forward(route, request):
        url = request.url
        path = url.split(BASE, 1)[1]
        if request.method != "GET":
            mutations.append(f"{request.method} {path.split('?')[0]}")
        fetched = route.fetch(url=FIX + path)
        if request.method == "POST" and path.endswith("/sol-credentials") and persist_then_500["armed"]:
            persist_then_500["armed"] = False
            persist_then_500["fired"] += 1
            route.fulfill(status=500, content_type="application/json", body=json.dumps({"detail": {"code": "SOL_CREDENTIAL_STORE_FAILED"}}))  # el servidor SI guardo
            return
        bodies.append(fetched.text())
        route.fulfill(response=fetched)

    page.route("**/api/v1/**", forward)
    login = ctx.request.post(FIX + "/api/v1/auth/login", data={"email": ADMIN, "password": PASSWORD})
    assert login.status == 200, login.status
    csrf = next(c["value"] for c in ctx.cookies() if c["name"] == "csrf_token")
    h = {"X-CSRF-Token": csrf}
    # --- preparacion SINTETICA por API (vault + secundaria ficticia)
    init = ctx.request.post(FIX + "/api/v1/sol-vault/initialize", data={"personal_vault_password": VAULT_PASSWORD}, headers=h)
    assert init.status == 200, init.status
    assert ctx.request.post(FIX + "/api/v1/sol-vault/recovery/confirm", data={"recovery_code": init.json()["recovery_code"]}, headers=h).status == 200
    sec = ctx.request.post(FIX + f"/api/v1/clients/{CID}/sol-credentials", headers=h, data={
        "credential_kind": "SECONDARY", "sol_username": SEC_USER, "sol_password": SEC_PASS, "authorization_basis": "Autorizacion ficticia secundaria"})
    assert sec.status == 200, sec.text()
    sec_id = sec.json()["id"]
    secondary_before = q(f"select id||'|'||version||'|'||status||'|'||md5(sol_username_encrypted)||'|'||md5(sol_password_encrypted)||'|'||updated_at from client_sol_credentials where id='{sec_id}'")

    def type_into(label_re: str, text: str, secret: bool = False) -> None:
        loc = page.get_by_label(re.compile(label_re), exact=False).first
        loc.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        loc.press_sequentially(text, delay=2)

    def fill_form(kind: str, name: str, user: str, password: str, basis: str, scope: str = "") -> None:
        page.get_by_label("Tipo de credencial").select_option(kind)
        type_into(r"Nombre interno del perfil", name)
        type_into(r"^Usuario SOL", user)
        type_into(r"^Contraseña SOL", password, True)
        type_into(r"Motivo de autorización", basis)
        if scope:
            type_into(r"Descripción del alcance", scope)

    page.goto(f"{BASE}/clients/{CID}/sol-credential")
    page.wait_for_selector("text=Agregar otro perfil de credencial", timeout=30000)
    check("lista_inicial_solo_secundaria", page.locator("[data-testid^='profile-']").count() == 1 and "Credencial secundaria" in page.content())

    # 1) El motivo largo (la causa de la incidencia): el campo lo limita a 60 caracteres y NO se envia nada que el servidor vaya a rechazar
    fill_form("PRIMARY_OWN_RUC", "SOL Principal (ficticia)", PRI_USER, PRI_PASS, LONG_BASIS)
    basis_value = page.get_by_label(re.compile("Motivo de autorización")).first.input_value()
    check("motivo_limitado_a_60", len(basis_value) == 60, f"len={len(basis_value)}")
    type_into(r"Motivo de autorización", "Autorizado por el titular")
    type_into(r"Descripción del alcance", LONG_BASIS)

    # 2) Doble clic rapido: exactamente UN alta
    before_mut = len(mutations)
    button = page.get_by_role("button", name="Registrar credencial")
    button.dblclick()
    page.wait_for_selector("text=Credencial guardada", timeout=30000)
    posts = [m for m in mutations[before_mut:] if m.endswith("/sol-credentials")]
    check("doble_clic_un_solo_alta", len(posts) == 1, str(posts))
    check("lista_con_ambos_perfiles", page.locator("[data-testid^='profile-']").count() == 2)
    content = page.content()
    check("perfil_nuevo_con_estado_real", "SOL Principal (ficticia)" in content and "principal" in content.lower())
    check("formulario_limpio_tras_guardar", page.get_by_label(re.compile(r"^Usuario SOL")).first.input_value() == "")

    # 3) Refrescar pantalla: persiste y la secundaria sigue
    page.reload()
    page.wait_for_selector("[data-testid^='profile-']", timeout=30000)
    check("tras_refrescar_ambos_perfiles", page.locator("[data-testid^='profile-']").count() == 2)
    check("secundaria_intacta_en_bd", q(f"select id||'|'||version||'|'||status||'|'||md5(sol_username_encrypted)||'|'||md5(sol_password_encrypted)||'|'||updated_at from client_sol_credentials where id='{sec_id}'") == secondary_before)
    kinds = sorted(q("select credential_kind from client_sol_credentials order by credential_kind").split())
    check("bd_tiene_secundaria_y_principal", kinds == ["PRIMARY_OWN_RUC", "SECONDARY"], str(kinds))
    check("cifrado_en_bd", q(f"select position('{PRI_PASS}'::bytea in sol_password_encrypted) from client_sol_credentials where credential_kind='PRIMARY_OWN_RUC'") == "0")

    # 4) Fallo ambiguo: el servidor guarda y la respuesta llega como 500 -> la UI relee, informa que YA estaba guardada y no pide reenviar
    persist_then_500["armed"] = True
    fill_form("SECONDARY", "Secundaria adicional (ficticia)", TWO_USER, TWO_PASS, "Autorizado por el titular")
    page.get_by_role("button", name="Registrar credencial").click()
    page.wait_for_selector("text=ya estaba guardada", timeout=30000)
    check("fallo_ambiguo_detecta_perfil_guardado", persist_then_500["fired"] == 1 and page.locator("[data-testid^='profile-']").count() == 3)
    check("fallo_ambiguo_no_duplica", q("select count(*) from client_sol_credentials where lower(profile_name)=lower('Secundaria adicional (ficticia)')") == "1")

    # 5) Perfil principal duplicado: error comprensible (409 controlado), sin tocar nada
    fill_form("PRIMARY_EXCEPTIONAL", "Otra principal", "OTROUSR4", "Otra-Clave-Ficticia-4", "Autorizado por el titular")
    page.get_by_role("button", name="Registrar credencial").click()
    page.wait_for_selector("text=ya tiene una credencial principal activa", timeout=30000)
    check("segunda_principal_rechazada_con_mensaje", q("select count(*) from client_sol_credentials") == "3")

    # 6) Boveda bloqueada: mensaje especifico, datos conservados en el formulario, sin alta
    ctx.request.post(FIX + "/api/v1/sol-vault/lock", headers=h)
    fill_form("SECONDARY", "Secundaria con boveda bloqueada", "BLOQUEADAUSR5", "Bloqueada-Clave-Ficticia-5", "Autorizado por el titular")
    page.get_by_role("button", name="Registrar credencial").click()
    page.wait_for_selector("text=bloqueada", timeout=30000)
    check("vault_bloqueada_mensaje_especifico", q("select count(*) from client_sol_credentials") == "3")
    check("formulario_conserva_datos_tras_error", page.get_by_label(re.compile(r"^Usuario SOL")).first.input_value() == "BLOQUEADAUSR5")

    # 7) Secretos y accesos externos
    leaked = [s for s in SECRETS if any(s in b for b in bodies)] + [s for s in SECRETS if s in page.evaluate("document.body.innerText")]
    check("sin_secretos_en_respuestas_ni_dom", not leaked, str(leaked))
    check("sin_llamadas_a_sol_access_sessions", not any("/sol-access/sessions" in m for m in mutations), str(mutations))

    # 8) Movil 375 sin desborde horizontal
    page.set_viewport_size({"width": 375, "height": 800})
    page.reload()
    page.wait_for_selector("[data-testid^='profile-']", timeout=30000)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("movil_375_sin_desborde", overflow <= 1, f"overflow={overflow}")
    browser.close()

failed = [k for k, v in results.items() if v != "PASS"]
print("RESULT", "PASS" if not failed else f"FAIL {failed}", f"({len(results)} comprobaciones)")
sys.exit(1 if failed else 0)
