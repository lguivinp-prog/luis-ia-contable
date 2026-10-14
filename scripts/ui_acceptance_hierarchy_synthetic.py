"""Chromium REAL + frontend REAL + backend REAL con datos SINTETICOS (BD `m2credfix`, backend de fixtures en :8012, sin broker ni agente): jerarquia de estados del
Centro SUNAT tras una prueba fallida SEGUIDA de una exitosa, y prueba por perfil.

Las respuestas de /acceptances y los flags `proven/available` de /credential-destinations se SIMULAN en el navegador (declarado): el servidor de fixtures no tiene
broker para ejecutar sesiones. La logica real de esas respuestas esta cubierta por los tests de integracion del backend; aqui se verifica SOLO la interfaz.
No se contacta SUNAT, ni el agente, ni el backend real."""
from __future__ import annotations

import json
import re
import subprocess
import sys

from playwright.sync_api import sync_playwright

BASE, FIX = "http://127.0.0.1:3010", "http://127.0.0.1:8012"
ADMIN, PASSWORD, VAULT = "admin@fixture-ficticio.com", "Fixture-Only-Pass-123!", "Boveda-Ficticia-Solo-Pruebas-77!"
ACCEPTED = ("SIRE_RVIE", "SIRE_RCE", "DECLARACIONES_Y_PAGOS")


def q(sql: str) -> str:
    return subprocess.run(["docker", "exec", "m2-native-pg", "psql", "-U", "m2native", "-d", "m2credfix", "-At", "-c", sql], capture_output=True, text=True).stdout.strip()


CID = q("select id from clients limit 1")
results: dict[str, str] = {}


def check(name: str, ok: bool, detail: str = "") -> None:
    results[name] = "PASS" if ok else f"FAIL {detail}"
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail and not ok else ""), flush=True)


mock = {"acceptances": [], "proven_profile": None}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1100, "height": 1000})
    page = ctx.new_page()

    def forward(route, request):
        path = request.url.split(BASE, 1)[1]
        if request.method == "GET" and path.endswith("/acceptances"):
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"acceptances": mock["acceptances"]}))
            return
        fetched = route.fetch(url=FIX + path)
        if request.method == "GET" and path.endswith("/credential-destinations") and mock["proven_profile"]:
            body = fetched.json()
            for cred in body["credentials"]:
                if cred["credential_id"] == mock["proven_profile"]:
                    for d in cred["destinations"]:
                        if d["destination"] in ACCEPTED:
                            d["proven"], d["available"] = True, True
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
            return
        route.fulfill(response=fetched)

    page.route("**/api/v1/**", forward)
    login = ctx.request.post(FIX + "/api/v1/auth/login", data={"email": ADMIN, "password": PASSWORD})
    assert login.status == 200
    h = {"X-CSRF-Token": next(c["value"] for c in ctx.cookies() if c["name"] == "csrf_token")}
    init = ctx.request.post(FIX + "/api/v1/sol-vault/initialize", data={"personal_vault_password": VAULT}, headers=h)
    assert init.status == 200, init.status
    assert ctx.request.post(FIX + "/api/v1/sol-vault/recovery/confirm", data={"recovery_code": init.json()["recovery_code"]}, headers=h).status == 200
    ids = {}
    for kind, name, user, pwd in (("PRIMARY_OWN_RUC", "Credencial principal", "PRINCIPALUSR2", "Principal-Clave-Ficticia-2"), ("SECONDARY", "Credencial secundaria", "SECUNDARIAUSR1", "Secundaria-Clave-Ficticia-1")):
        r = ctx.request.post(FIX + f"/api/v1/clients/{CID}/sol-credentials", headers=h, data={
            "credential_kind": kind, "profile_name": name, "sol_username": user, "sol_password": pwd, "authorization_basis": "Autorizacion ficticia"})
        assert r.status == 200, r.text()
        ids[kind] = r.json()["id"]
    primary, secondary = ids["PRIMARY_OWN_RUC"], ids["SECONDARY"]
    mock["proven_profile"] = primary

    def row(rid, destination, phase, created, credential, source=None, diagnostic=None):
        return {"id": rid, "client_id": CID, "credential_id": credential, "destination": destination,
                "status": "CONSUMED", "created_at": created, "expires_at": created, "consumed_at": created, "session_id": rid,
                "result": "PASS" if phase == "CONFIRMED_PASS" else "FAIL" if phase in {"CONFIRMED_FAIL"} else None, "result_note": None, "phase": phase,
                "session_status": "SUCCEEDED" if phase == "CONFIRMED_PASS" else "FAILED", "session_operation_result": "DESTINATION_REACHED" if phase == "CONFIRMED_PASS" else None,
                "diagnostic_code": diagnostic, "result_source": source}

    # historial real de la aceptacion: DYP fallo (sistema) y luego paso; RVIE y RCE pasaron
    mock["acceptances"] = [
        row("a4", "DECLARACIONES_Y_PAGOS", "CONFIRMED_PASS", "2026-10-14T09:36:00Z", primary, "OWNER"),
        row("a3", "SIRE_RCE", "CONFIRMED_PASS", "2026-10-14T03:47:00Z", primary, "OWNER"),
        row("a2", "DECLARACIONES_Y_PAGOS", "CONFIRMED_FAIL", "2026-10-14T03:49:00Z", primary, "SYSTEM", "AUTH_NAVIGATION_STALLED"),
        row("a1", "SIRE_RVIE", "CONFIRMED_PASS", "2026-10-14T03:42:00Z", primary, "OWNER"),
    ]
    page.goto(f"{BASE}/clients/{CID}/sol-access")
    page.wait_for_selector("[data-testid='acceptance-panel']", timeout=40000)
    page.locator("[data-testid='acceptance-profile']").select_option(primary)
    page.wait_for_timeout(500)

    check("sin_alerta_roja_antigua_tras_exito_posterior", page.locator("[data-testid='acceptance-failed']").count() == 0)
    check("bloque_de_pruebas_superadas_visible", page.locator("[data-testid='acceptance-validated']").count() == 1
          and all(t in page.inner_text("[data-testid='acceptance-validated']") for t in ("RVIE", "RCE", "Declaraciones y Pagos")))
    page.locator("details summary", has_text="Historial de pruebas").click()
    history = page.inner_text("details:has(summary:has-text('Historial de pruebas'))")
    check("historial_conserva_fallo_y_exitos", history.count("Declaraciones y Pagos") == 2 and "falló antes de llegar" in history and "confirmada como correcta" in history)
    for d in ACCEPTED:
        check(f"perfil_probado_{d}_sin_nueva_autorizacion", page.locator(f"[data-testid='acceptance-proven-{d}']").count() == 1)
    body = page.inner_text("body")
    check("generacion_manual_visible", "manual" in page.inner_text("[data-testid='acceptance-manual-SIRE_GENERACION_REGISTROS']").lower())
    check("sin_boton_de_confirmacion_en_fallos", page.get_by_role("button", name="Correcta").count() == 0)

    # botones reales de apertura para el perfil probado
    page.get_by_label(re.compile("Credencial principal")).first.check()  # el usuario ELIGE el perfil; nada se preselecciona
    page.wait_for_timeout(500)
    labels = [b.inner_text() for b in page.get_by_role("button").all()]
    check("botones_apertura_de_los_tres_destinos_para_el_perfil_probado",
          all(any(f"Abrir {name}" in label for label in labels) for name in ("SIRE · Registro de Ventas (RVIE)", "SIRE · Registro de Compras (RCE)", "Declaraciones y Pagos")), str(labels))
    check("generacion_sin_boton_de_apertura", not any("Generación" in label and "Abrir" in label for label in labels))

    # perfil NO probado: ninguna apertura, estado explicito y sin contradiccion
    page.get_by_label(re.compile("Credencial secundaria")).first.check()
    page.wait_for_timeout(500)
    labels2 = [b.inner_text() for b in page.get_by_role("button").all()]
    check("perfil_no_probado_no_ofrece_apertura_de_destinos_aceptados", not any(f"Abrir {n}" in l for l in labels2 for n in ("SIRE · Registro de Ventas (RVIE)", "SIRE · Registro de Compras (RCE)", "Declaraciones y Pagos")), str(labels2))
    other = page.inner_text("[data-testid='unvalidated-destinations']")
    check("perfil_no_probado_explica_que_requiere_prueba", "Requiere prueba con este perfil" in other and "No validado" in other, other[:300])
    check("seleccion_no_cambia_de_perfil_sola", page.get_by_label(re.compile("Credencial secundaria")).first.is_checked())

    # Workspace mensual: mismo criterio por perfil
    period = q("select id from tax_periods limit 1")
    page.get_by_label(re.compile("Credencial principal")).first.check()
    page.goto(f"{BASE}/cockpit/{CID}?period={period}&tab=resumen")
    page.wait_for_selector("#sunat-accesos", timeout=40000)
    page.wait_for_timeout(800)
    cards = {d: page.locator(f"[data-testid='dest-{d}']") for d in ACCEPTED}
    states = {d: c.get_attribute("data-state") for d, c in cards.items() if c.count()}
    check("workspace_con_perfiles_multiples_exige_elegir_o_muestra_estado", set(states) == set(ACCEPTED), str(states))
    page.set_viewport_size({"width": 375, "height": 800})
    page.reload()
    page.wait_for_selector("#sunat-accesos", timeout=40000)
    check("movil_375_sin_desborde", page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1)
    browser.close()

failed = [k for k, v in results.items() if v != "PASS"]
print("RESULT", "PASS" if not failed else f"FAIL {failed}", f"({len(results)} comprobaciones)")
sys.exit(1 if failed else 0)
