"""Traza estructural de autenticacion: forma de ruta saneada, motivo del estancamiento deducido solo de la evidencia y cero fugas."""

from windows_agent import auth_trace as at


def test_path_shape_is_an_allowlist_and_everything_else_becomes_an_opaque_category():
    shape = at.path_shape("/v1/clientessol/20123456786/oauth2/JUAN-PEREZ/juan.perez@example.com/CODE-SECRET-123/welcome")
    assert shape == ["v1", "clientessol", "{num<=16}", "oauth2", "{mixed<=16}", "{mixed>16}", "{mixed<=16}", "welcome"]
    assert at.path_shape("/Maria") == ["{alpha<=8}"] and at.path_shape("/abc123") == ["{alnum<=8}"]
    joined = " ".join(at.path_shape("/20123456786/JUAN-PEREZ/token-abc"))
    assert "2012" not in joined and "JUAN" not in joined and "abc" not in joined


def test_scrub_redacts_urls_long_digit_runs_emails_and_oversized_strings():
    node = {"a": "https://x.example/p", "b": "20123456786", "c": "me@example.com", "d": "x" * 80, "e": ["ok", "123456"], "f": {"n": 7, "s": "route"}}
    out = at.scrub(node)
    assert out["a"] == out["b"] == out["c"] == out["d"] == "<redacted>" and out["e"] == ["ok", "<redacted>"]
    assert out["f"] == {"n": 7, "s": "route"}
    big = at.scrub({str(i): i for i in range(500)})
    assert len(big) <= 120


def test_path_shape_keeps_route_names_and_hides_ids_and_values():
    assert at.path_shape("/v1/clientessol/0c9d1f5e-1234-4a2b-9f00-aaaaaaaaaaaa/oauth2/loginMenuSol") == ["v1", "clientessol", "{mixed>16}", "oauth2", "loginMenuSol"]
    assert at.path_shape("/cl-ti-itmenu/MenuInternet.htm") == ["cl-ti-itmenu", "MenuInternet.htm"]
    assert at.path_shape("/a1b2c3d4e5f6a7b8") == ["{alnum<=16}"]
    assert at.path_shape("") == []
    assert "secret123token" not in " ".join(at.path_shape("/ok/12345678901/secret123token"))


def test_status_class_is_a_coarse_bucket():
    assert at.status_class(204) == "2xx" and at.status_class(404) == "4xx" and at.status_class(503) == "5xx" and at.status_class(7) == "other"


def _summary(counters=None, pending=0, status=200):
    return {"counters": counters or {}, "pending_documents": pending, "last_document": {"status": status}}


def test_stall_reason_is_deduced_only_from_evidence_in_a_fixed_priority():
    complete = {"ready_state": "complete"}
    assert at.stall_reason(_summary(status=500), complete) == "SERVER_ERROR_DOCUMENT"
    assert at.stall_reason(_summary(pending=1), complete) == "NAVIGATION_PENDING_NO_RESPONSE"
    assert at.stall_reason(_summary(), {"ready_state": "pending_navigation"}) == "NAVIGATION_PENDING_NO_RESPONSE"
    assert at.stall_reason(_summary({"script:4xx:auth": 1, "failed:script:auth": 1}), complete) == "RESOURCE_HTTP_ERROR"
    assert at.stall_reason(_summary({"failed:script:other": 1}), complete) == "RESOURCE_LOAD_FAILED"
    assert at.stall_reason(_summary({"js_errors": 2}), complete) == "SCRIPT_ERROR"
    assert at.stall_reason(_summary({"dialog_alert": 1}), complete) == "DIALOG_DISMISSED_BY_BROWSER"
    assert at.stall_reason(_summary(), complete) == "STABLE_DOCUMENT_NO_CONTINUATION"
    assert at.stall_reason(_summary(), None) == "UNDETERMINED"


def test_document_state_is_reduced_to_numbers_and_booleans():
    state = at.sanitize_document_state({"ready_state": "<script>", "webdriver_flag": 1, "has_opener": True, "history_length": 3, "body_children": "x",
                                         "scripts_total": 2, "scripts_external": True, "resources_loaded": 5.0, "visible_area": 100})
    assert state["ready_state"] == "unknown" and state["webdriver_flag"] is True and state["body_children"] == -1
    assert state["scripts_external"] == -1 and state["resources_loaded"] == 5 and state["history_length"] == 3



def test_stall_reason_after_submission_ignores_everything_that_happened_on_the_public_entry_page():
    """Incidencia real de Declaraciones y Pagos: dos `fetch` fallidos y errores de script de la pagina PUBLICA (antes del login) se tomaron como causa de una
    ventana de autenticacion que en realidad estaba estable. Despues del envio solo cuentan los contadores `post:`."""
    complete = {"ready_state": "complete"}
    noisy = {"failed:fetch:entry": 2, "js_errors": 5, "console_error": 3, "document:2xx:auth": 2, "post:document:2xx:auth": 2}
    assert at.stall_reason({**_summary(noisy), "submitted": True}, complete) == "STABLE_DOCUMENT_NO_CONTINUATION"
    assert at.stall_reason(_summary(noisy), complete) == "RESOURCE_LOAD_FAILED"  # sin fase (comportamiento previo) el ruido previo si contaba
    real = {**noisy, "post:failed:script:other": 1}
    assert at.stall_reason({**_summary(real), "submitted": True}, complete) == "RESOURCE_LOAD_FAILED"  # una causa POSTERIOR al envio se sigue distinguiendo


def test_trace_counts_both_globally_and_per_phase():
    trace = at.AuthTrace(contract=None, clock=lambda: 0.0, host_of=lambda url: ("https", "x"))
    trace._bump("js_errors")
    trace.mark_submitted()
    trace._bump("js_errors")
    trace._bump("failed:fetch:auth")
    counters = trace.summary()["counters"]
    assert counters["js_errors"] == 2 and counters["post:js_errors"] == 1 and counters["post:failed:fetch:auth"] == 1 and trace.summary()["submitted"] is True


def test_document_state_keeps_only_categories_and_allowlisted_query_key_names():
    raw = {"ready_state": "complete", "title": "Bienvenidos a SUNAT", "pathname": "/v1/clientessol/12345678901/oauth2/loginMenuSol", "headings": 1,
           "query_keys": ["error", "code", "20123456789", "zzz_secret"], "has_hash": True, "opener_closed": False}
    doc = at.sanitize_document_state(raw)
    assert doc["title_key"] == "welcome" and doc["query_keys"] == ["code", "error"] and doc["query_other_keys"] == 2
    assert doc["route"][:3] == ["v1", "clientessol", "{num<=16}"] and "20123456789" not in repr(doc) and "zzz_secret" not in repr(doc) and "Bienvenidos" not in repr(doc)
    assert at.sanitize_document_state({"ready_state": "complete", "title": ""})["title_key"] == "empty"
    assert at.sanitize_document_state({"ready_state": "complete", "title": "SUNAT - Menú SOL"})["title_key"] == "menu_sol"
