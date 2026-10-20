"""Chromium REAL + frontend REAL + backend REAL con datos SINTETICOS: expediente de CONTROL SIRE (M4) en el workspace mensual.

Requisitos: `m2-live/cred_fixture_up.ps1` (BD aislada `m2credfix`, backend de fixtures en :8012 sin broker ni agente) y el frontend en :3010. Las llamadas /api/v1 del navegador
se reenvian al backend de fixtures (nada llega al backend real, a la base real ni a SUNAT). Todos los datos y archivos son FICTICIOS.

Recorre el criterio de exito de M4: abrir el workspace de un contribuyente, elegir periodo, incorporar dos fuentes SIRE (manual y CSV), compararlas, detectar diferencias,
registrar la revision profesional, conservar evidencia y volver a consultarlo -- sin modificar ningun estado legal de presentacion."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE, FIX = "http://127.0.0.1:3010", "http://127.0.0.1:8012"
ADMIN, ASSISTANT, PASSWORD = "admin@fixture-ficticio.com", "asistente@fixture-ficticio.com", "Fixture-Only-Pass-123!"


def q(sql: str) -> str:
    return subprocess.run(["docker", "exec", "m2-native-pg", "psql", "-U", "m2native", "-d", "m2credfix", "-At", "-c", sql], capture_output=True, text=True).stdout.strip()


q("update clients set service_status='activo'")
CID = q("select id from clients limit 1")
PID = q("select id from tax_periods limit 1")
results: dict[str, str] = {}
console_errors: list[str] = []
posts: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results[name] = "PASS" if ok else f"FAIL {detail}"
    print(("PASS " if ok else "FAIL ") + name + (f" :: {detail}" if detail and not ok else ""), flush=True)


def tax_snapshot() -> str:
    return q("select (select count(*) from declarations)||'|'||(select count(*) from tax_payments)||'|'||(select count(*) from client_obligations)||'|'||(select count(*) from client_period_operations)"
             "||'|'||(select count(*) from tax_assessments)||'|'||(select count(*) from declarations where filing_status='FILED')")


workdir = Path(tempfile.mkdtemp(prefix="m4-sintetico-"))
csv_darwin = workdir / "darwin_rce.csv"
csv_darwin.write_text("Periodo;Tipo;Base Imponible;IGV;Importe Total;Comprobante\n202609;Compras;100,00;18,00;118,00;F001-1\n202609;Compras;200,50;36,09;236,59;F001-2\n", encoding="utf-8")
pdf = workdir / "constancia.pdf"
pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 3 3]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
tax_before = tax_snapshot()

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = ctx.new_page()
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" and "favicon" not in m.text else None)

    def forward(route, request):
        path = request.url.split(BASE, 1)[1]
        if request.method != "GET":
            posts.append(f"{request.method} {path.split('?')[0]}")
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("multipart/form-data"):
            # Chromium NO expone el contenido de los archivos en la peticion interceptada: se reconstruye el multipart desde Python
            # con los campos de texto del formulario y el archivo local (sintetico) que el navegador selecciono.
            body = (request.post_data_buffer or b"").decode("utf-8", "replace")
            fields = dict(re.findall(r'name="([^"]+)"\r\n\r\n(.*?)\r\n--', body, flags=re.S))
            filename = re.search(r'filename="([^"]+)"', body).group(1)
            multipart = {k: v for k, v in fields.items()} | {"file": {"name": filename, "mimeType": "application/octet-stream", "buffer": (workdir / filename).read_bytes()}}
            resp = ctx.request.post(FIX + path, multipart=multipart, headers={"x-csrf-token": csrf})
            route.fulfill(status=resp.status, headers={"content-type": resp.headers.get("content-type", "application/json")}, body=resp.body())
            return
        route.fulfill(response=route.fetch(url=FIX + path))

    page.route("**/api/v1/**", forward)
    login = ctx.request.post(FIX + "/api/v1/auth/login", data={"email": ADMIN, "password": PASSWORD})
    assert login.status == 200
    csrf = next(c["value"] for c in ctx.cookies() if c["name"] == "csrf_token")

    def goto_sire() -> None:
        page.goto(f"{BASE}/cockpit/{CID}?period={PID}&tab=sire")
        page.wait_for_selector("[data-testid='sire-control-panel'] [data-testid='sire-control-RVIE']", timeout=60000)

    def state(component: str) -> str:
        return page.locator(f"[data-testid='sire-state-{component}']").inner_text()

    goto_sire()
    check("consultar_no_crea_expedientes", q("select count(*) from sire_controls") == "0" and page.locator("[data-testid='sire-control-RVIE']").get_attribute("data-summary") == "NO_CONTROL")
    check("dos_secciones_independientes", page.locator("[data-testid='sire-control-RVIE']").count() == 1 and page.locator("[data-testid='sire-control-RCE']").count() == 1
          and "Registro de Ventas" in page.inner_text("[data-testid='sire-control-RVIE']") and "Registro de Compras" in page.inner_text("[data-testid='sire-control-RCE']"))
    check("advertencia_oficial_una_sola_vez_en_el_panel", page.inner_text("[data-testid='sire-control-panel']").lower().count("no es una constancia oficial") == 1)
    check("acceso_sunat_m3_conservado", page.locator("[data-testid='dest-SIRE_RVIE']").count() == 1 and page.locator("[data-testid='dest-SIRE_RCE']").count() == 1
          and page.locator("[data-testid='dest-DECLARACIONES_Y_PAGOS']").count() == 1)

    def fill(section, label, value):
        loc = section.get_by_label(re.compile(label)).first
        loc.click()
        loc.fill(value)

    # --- RVIE: fuente SUNAT manual, solo con cantidad y total (IGV/base desconocidos) ---
    rvie = page.locator("[data-testid='sire-control-RVIE']")
    rvie.locator("[data-testid='source-RVIE-SUNAT']").get_by_role("button", name="Ingresar datos").click()
    form = page.locator("[data-testid='source-form-RVIE-SUNAT']")
    fill(form, "Cantidad de registros", "120")
    fill(form, "Importe total", "11800.00")
    form.get_by_role("button", name="Guardar fuente").click()  # sin identificacion de fuente: debe rechazarlo la validacion previa
    check("validacion_previa_identificacion_obligatoria", form.get_by_text("Identifica la fuente").count() >= 1)
    fill(form, "Identificación de la fuente", "Reporte RVIE septiembre")
    form.get_by_role("button", name="Guardar fuente").dblclick()  # doble clic: una sola fuente
    page.wait_for_selector("[data-testid='sire-state-RVIE']:has-text('Información parcial')", timeout=30000)
    check("doble_clic_una_sola_fuente", q("select count(*) from sire_control_sources") == "1")
    src = page.inner_text("[data-testid='source-RVIE-SUNAT']")
    check("desconocido_no_es_cero", "desconocido" in src and "S/ 0.00" not in src and "Base" in src)
    check("comparacion_pendiente_con_una_fuente", "Comparación pendiente" in page.inner_text("[data-testid='comparison-RVIE']"))

    # --- RVIE: fuente DARWIN equivalente -> cuadra ---
    rvie.locator("[data-testid='source-RVIE-DARWIN']").get_by_role("button", name="Ingresar datos").click()
    form = page.locator("[data-testid='source-form-RVIE-DARWIN']")
    fill(form, "Cantidad de registros", "120")
    fill(form, "Importe total", "11800.00")
    fill(form, "Identificación de la fuente", "Export DARWIN septiembre")
    form.get_by_role("button", name="Guardar fuente").click()
    page.wait_for_selector("[data-testid='sire-state-RVIE']:has-text('Cuadra')", timeout=30000)
    check("cuadra_exacto", "Cuadra" in state("RVIE") and "Falta tu revisión" not in page.inner_text("[data-testid='comparison-RVIE']"))
    # revision profesional
    review = page.locator("[data-testid='review-RVIE']")
    review.get_by_label("Verificado por el contador").check()
    review.get_by_role("button", name="Registrar revisión").click()
    page.wait_for_selector("[data-testid='sire-state-RVIE']:has-text('Cuadra · revisado')", timeout=30000)
    check("revision_registrada", "Verificado por el contador" in page.inner_text("[data-testid='review-current-RVIE']") and q("select count(*) from sire_control_reviews where status='CURRENT'") == "1")

    # --- reemplazo: nueva version DARWIN con otro total -> requiere nueva revision y la anterior queda en el historial ---
    rvie.locator("[data-testid='source-RVIE-DARWIN']").get_by_role("button", name="Reemplazar (manual)").click()
    form = page.locator("[data-testid='source-form-RVIE-DARWIN']")
    fill(form, "Importe total", "11700.00")
    fill(form, "Identificación de la fuente", "Export DARWIN corregido")
    form.get_by_role("button", name="Guardar nueva versión").click()
    page.wait_for_selector("[data-testid='sire-stale-RVIE']", timeout=30000)
    check("reemplazo_exige_nueva_revision", "Requiere nueva revisión" in state("RVIE") and "11,700.00" in page.inner_text("[data-testid='comparison-RVIE']"))
    check("revision_previa_preservada", q("select count(*) from sire_control_reviews where status='CURRENT' and version=1") == "1")
    rvie.get_by_role("button", name=re.compile("Historial")).click()
    page.wait_for_selector("[data-testid='sire-history'] li", timeout=15000)
    hist = page.inner_text("[data-testid='sire-history']")
    check("historial_muestra_versiones_y_revision", "v2" in hist and "reemplazada" in hist and "Revisión v1" in hist, hist)

    # --- RCE: SUNAT manual + DARWIN por CSV con mapeo, diferencias reales y observacion ---
    rce = page.locator("[data-testid='sire-control-RCE']")
    rce.locator("[data-testid='source-RCE-SUNAT']").get_by_role("button", name="Ingresar datos").click()
    form = page.locator("[data-testid='source-form-RCE-SUNAT']")
    fill(form, "Cantidad de registros", "3")
    fill(form, "Base / importe comparable", "300.50")
    fill(form, "IGV", "54.09")
    fill(form, "Importe total", "354.59")
    fill(form, "Identificación de la fuente", "Reporte RCE septiembre")
    form.get_by_role("button", name="Guardar fuente").click()
    page.wait_for_selector("[data-testid='sire-state-RCE']:has-text('Información parcial')", timeout=30000)
    rce.locator("[data-testid='source-RCE-DARWIN']").get_by_role("button", name="Importar archivo").click()
    wizard = page.locator("[data-testid='import-wizard-RCE-DARWIN']")
    wizard.locator("[data-testid='import-file-input']").set_input_files(str(csv_darwin))
    wizard.get_by_role("button", name="Ver vista previa").click()
    try:
        page.wait_for_selector("[data-testid='import-preview']", timeout=30000)
    except Exception:
        print("DEBUG alertas:", wizard.locator("[role='alert']").all_inner_texts(), "posts:", posts[-3:], "consola:", console_errors[-3:], flush=True)
        raise
    preview_text = page.inner_text("[data-testid='import-preview']")
    check("vista_previa_con_encabezados_y_huella", "Base Imponible" in preview_text and "SHA-256" in preview_text and "delimitador «;»" in preview_text)
    check("mapeo_sugerido_no_inventado", wizard.get_by_label("Importe total").first.input_value() == "Importe Total")
    wizard.get_by_label("Separador decimal de los importes").select_option("COMMA")
    wizard.get_by_role("button", name="Importar como fuente").click()
    page.wait_for_selector("[data-testid='sire-state-RCE']:has-text('Presenta diferencias')", timeout=30000)
    comp = page.inner_text("[data-testid='comparison-RCE']")
    check("diferencias_sin_correccion_automatica", "−" in comp or "+" in comp)
    check("rce_importado_con_decimales_exactos", q("select total_amount::text||'|'||base_amount::text||'|'||record_count from sire_control_sources where component='RCE' and origin='DARWIN'") == "354.59|300.50|2")
    # diferencia: cantidad 2 vs 3 => -1 ; totales iguales
    check("diferencia_de_cantidad_visible", "Cantidad de registros" in comp and "−1" in comp)
    review = page.locator("[data-testid='review-RCE']")
    check("no_se_puede_verificar_con_diferencias", review.get_by_label("Verificado por el contador").count() == 0)
    review.get_by_label("Diferencias por regularizar").check()
    review.get_by_role("button", name="Registrar revisión").click()
    page.wait_for_selector("[data-testid='review-RCE'] [role='alert']:has-text('observación')", timeout=15000)
    check("observacion_obligatoria", q("select count(*) from sire_control_reviews where component='RCE'") == "0")
    review.get_by_label(re.compile("Observación")).fill("Falta registrar una factura de compra")
    review.get_by_role("button", name="Registrar revisión").click()
    page.wait_for_selector("[data-testid='sire-state-RCE']:has-text('Diferencias · revisado')", timeout=30000)

    # --- evidencia: PDF, descarga autorizada, integridad y duplicado ---
    evidence = page.locator("[data-testid='evidence-RCE']")
    if evidence.get_attribute("open") is None:
        evidence.locator("summary").click()
    evidence.locator("[data-testid='evidence-input-RCE']").set_input_files(str(pdf))
    evidence.get_by_role("button", name="Adjuntar").click()
    page.wait_for_selector("[data-testid='evidence-RCE'] li:has-text('constancia.pdf')", timeout=30000)
    evidence.locator("li:has-text('constancia.pdf')").get_by_role("button", name="Verificar integridad").click()
    page.wait_for_selector("[data-testid='evidence-RCE'] [role='status']:has-text('íntegro')", timeout=15000)
    check("evidencia_conservada_con_integridad", True)
    evidence.locator("[data-testid='evidence-input-RCE']").set_input_files(str(pdf))
    evidence.get_by_role("button", name="Adjuntar").click()
    page.wait_for_selector("text=no se duplicó", timeout=15000)
    check("duplicado_no_se_duplica", q("select count(*) from sire_control_files where purpose='EVIDENCE'") == "1")
    href = evidence.locator("li:has-text('constancia.pdf') a").get_attribute("href")
    dl = ctx.request.get(FIX + href)
    check("descarga_autorizada", dl.status == 200 and dl.body().startswith(b"%PDF"))
    check("sin_rutas_fisicas_en_la_pagina", "document_storage" not in page.content() and "sire/" not in page.inner_text("[data-testid='evidence-RCE']"))

    # --- persistencia: otra carga ve lo mismo ---
    goto_sire()
    check("persistencia_al_recargar", "Cuadra · revisado" not in state("RVIE") and "Requiere nueva revisión" in state("RVIE") and "Diferencias · revisado" in state("RCE"))

    # --- reporte ---
    control_id = q("select id from sire_controls where component='RCE'")
    page.goto(f"{BASE}/clients/{CID}/sire-control/{control_id}")
    page.wait_for_selector("[data-testid='sire-report']", timeout=30000)
    rep = page.inner_text("[data-testid='sire-report']")
    check("reporte_con_advertencia_oficial", "no es una constancia oficial" in rep.lower() or "NO constituye constancia oficial" in rep)
    check("reporte_con_fuentes_diferencias_archivos_y_revision", all(t in rep for t in ("Reporte RCE septiembre", "Cantidad de registros", "constancia.pdf", "SHA-256", "Diferencias por regularizar", "huella del reporte")))
    check("reporte_sin_rutas_fisicas", "document_storage" not in rep and "/var/lib" not in rep)

    # --- cockpit multi-RUC: una fila por cliente con resumen, sin reemplazar el indicador juridico ---
    page.goto(f"{BASE}/cockpit?period={PID}")
    page.wait_for_selector(f"[data-testid='sire-control-cell-{CID}']", timeout=60000)
    cell = page.inner_text(f"[data-testid='sire-control-cell-{CID}']")
    check("cockpit_resume_rvie_y_rce", "RVIE: Requiere nueva revisión" in cell and "RCE: Diferencias · revisado" in cell, cell)
    headers = page.locator("table[aria-label='Clientes del periodo'] th").all_inner_texts()
    headers = [h.strip().upper() for h in headers]  # el CSS las muestra en mayusculas
    check("cockpit_conserva_columna_juridica_sire", "SIRE" in headers and "CONTROL SIRE" in headers and page.locator(f"tr:has([data-testid='sire-control-cell-{CID}'])").count() == 1,
          f"{headers} filas={page.locator(f'tr:has([data-testid=sire-control-cell-{CID}])').count()}")

    # --- responsive y accesibilidad basica ---
    for width in (375, 768, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        goto_sire()
        overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check(f"sin_desborde_horizontal_{width}", overflow <= 1, f"overflow={overflow}")
    check("secciones_con_encabezado_accesible", page.locator("section[aria-labelledby='sire-RVIE-title'] h3").count() == 1 and page.locator("table caption").count() >= 1)
    page.set_viewport_size({"width": 375, "height": 900})
    unlabeled = page.evaluate("""() => [...document.querySelectorAll('[data-testid=sire-control-panel] input, [data-testid=sire-control-panel] select, [data-testid=sire-control-panel] textarea')]
        .filter(e => e.type !== 'hidden' && !e.labels?.length && !e.getAttribute('aria-label')).length""")
    check("campos_con_etiqueta_asociada", unlabeled == 0, f"{unlabeled} sin etiqueta")

    # --- asistente: lectura, sin acciones de escritura ---
    ctx2 = browser.new_context(viewport={"width": 1440, "height": 1000})
    page2 = ctx2.new_page()
    page2.route("**/api/v1/**", lambda route, request: route.fulfill(response=route.fetch(url=FIX + request.url.split(BASE, 1)[1])))
    assert ctx2.request.post(FIX + "/api/v1/auth/login", data={"email": ASSISTANT, "password": PASSWORD}).status == 200
    page2.goto(f"{BASE}/cockpit/{CID}?period={PID}&tab=sire")
    page2.wait_for_selector("[data-testid='sire-control-RVIE']", timeout=60000)
    ro = page2.inner_text("[data-testid='sire-control-panel']")
    check("asistente_solo_lectura", "Ingresar datos" not in ro and "Importar archivo" not in ro and "Reemplazar" not in ro and "Registrar revisión" not in ro and "Reporte de control" in ro)

    # --- efectos ---
    check("sin_llamadas_mutables_a_sunat_ni_sol_access", not [x for x in posts if "sol-access" in x or "sol-vault" in x], str([x for x in posts if "sol-" in x]))
    check("estados_tributarios_intactos", tax_snapshot() == tax_before, f"{tax_before} -> {tax_snapshot()}")
    check("sin_errores_de_consola", not console_errors, str(console_errors[:3]))
    browser.close()

failed = [k for k, v in results.items() if v != "PASS"]
print("RESULT", "PASS" if not failed else f"FAIL {failed}", f"({len(results)} comprobaciones)")
sys.exit(1 if failed else 0)
