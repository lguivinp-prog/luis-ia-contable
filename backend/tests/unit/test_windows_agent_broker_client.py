"""Fase 5, Bloque 5.2, Domain E2 -- pruebas aisladas (sin broker real) de
`windows_agent/broker_client.py`: secuencia de backoff acotada, nunca
reintento del endpoint de credencial, limpieza de `CredentialMaterial`.
Mismo patron que `test_broker_client_backoff.py` de Spike B, adaptado a
la politica de reintento SEPARADA que exige el encargo E2 (secciones
56-58)."""

from __future__ import annotations

import httpx
import pytest

from windows_agent.broker_client import (
    BACKOFF_SEQUENCE_SECONDS,
    BrokerUnreachableError,
    CredentialMaterial,
    call_with_backoff,
)


def test_AA_backoff_retries_bounded_sequence_only_on_transport_errors():
    calls = []
    sleeps = []

    def _flaky():
        calls.append(1)
        if len(calls) < 3:
            raise httpx.ConnectError("simulated transient failure")
        return "ok"

    result = call_with_backoff(_flaky, sleep=lambda d: sleeps.append(d))
    assert result == "ok"
    assert len(calls) == 3
    assert len(sleeps) == 2  # dos reintentos antes del tercer intento exitoso


def test_AA_backoff_exhausts_and_raises_broker_unreachable():
    def _always_fails():
        raise httpx.ConnectTimeout("simulated permanent failure")

    sleeps = []
    with pytest.raises(BrokerUnreachableError):
        call_with_backoff(_always_fails, sleep=lambda d: sleeps.append(d))
    # acotado -- nunca infinito: exactamente len(BACKOFF_SEQUENCE_SECONDS) reintentos
    assert len(sleeps) == len(BACKOFF_SEQUENCE_SECONDS)


def test_AA_backoff_never_retries_non_transport_exceptions():
    """Un error de aplicacion (ej. una excepcion de validacion) nunca debe
    tratarse como fallo de transporte reintentable."""
    calls = []

    def _application_error():
        calls.append(1)
        raise ValueError("application-level error, not a transport failure")

    with pytest.raises(ValueError):
        call_with_backoff(_application_error, sleep=lambda d: None)
    assert len(calls) == 1  # nunca reintentado


def test_AU_credential_material_clear_drops_references():
    material = CredentialMaterial(ruc="20123456789", sol_username="u", sol_password="p")
    assert material.ruc == "20123456789"
    material._clear()
    assert material.ruc is None
    assert material.sol_username is None
    assert material.sol_password is None


def test_AC_credential_material_repr_never_leaks_plaintext():
    material = CredentialMaterial(
        ruc="20123456789", sol_username="TEST_SOL_USER", sol_password="TEST_SOL_PASSWORD_DO_NOT_USE"
    )
    text = repr(material)
    assert "20123456789" not in text
    assert "TEST_SOL_USER" not in text
    assert "TEST_SOL_PASSWORD_DO_NOT_USE" not in text
    assert str(material) == text


# --- E2.1-A: tope de respuesta de credencial aplicado EN STREAMING ---


def _client_with_transport(handler):
    import ssl
    import uuid as _uuid

    from windows_agent.broker_client import OperationalBrokerClient

    client = OperationalBrokerClient(
        operational_base_url="https://127.0.0.1:1",
        ssl_context=ssl.create_default_context(),
        protocol_version=1,
        timeout=1.0,
    )
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False)
    return client, _uuid.uuid4()


_GOOD_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff"}


def test_credential_response_oversized_is_aborted_while_streaming_never_fully_read():
    from windows_agent.broker_client import MAX_CREDENTIAL_RESPONSE_BYTES, BrokerProtocolError

    consumed = {"bytes": 0}

    def _endless():
        while True:  # un cuerpo SIN FIN: solo un lector acotado puede terminar
            consumed["bytes"] += 256
            yield b"x" * 256

    client, job_id = _client_with_transport(
        lambda request: httpx.Response(200, headers=_GOOD_HEADERS, content=_endless())
    )
    with pytest.raises(BrokerProtocolError) as excinfo:
        with client.fetch_credential_for_job(connection_id="c", job_id=job_id):
            pass
    assert "xxxx" not in str(excinfo.value)  # nunca ecoa el cuerpo recibido
    assert consumed["bytes"] <= MAX_CREDENTIAL_RESPONSE_BYTES + 512  # se aborto casi al cruzar el tope


def test_credential_response_declared_oversized_content_length_is_rejected_without_reading():
    from windows_agent.broker_client import BrokerProtocolError

    client, job_id = _client_with_transport(
        lambda request: httpx.Response(200, headers={**_GOOD_HEADERS, "Content-Length": "999999"}, content=b"{}")
    )
    with pytest.raises(BrokerProtocolError):
        with client.fetch_credential_for_job(connection_id="c", job_id=job_id):
            pass


def test_credential_response_malformed_json_rejected():
    from windows_agent.broker_client import BrokerProtocolError

    client, job_id = _client_with_transport(
        lambda request: httpx.Response(200, headers=_GOOD_HEADERS, content=b"{not json")
    )
    with pytest.raises(BrokerProtocolError):
        with client.fetch_credential_for_job(connection_id="c", job_id=job_id):
            pass


def test_credential_response_within_limit_is_accepted():
    import json as _json

    body = _json.dumps({"protocol_version": 1, "ruc": "20123456789", "sol_username": "U", "sol_password": "P"})
    assert len(body) < 1024
    client, job_id = _client_with_transport(
        lambda request: httpx.Response(200, headers=_GOOD_HEADERS, content=body.encode())
    )
    with client.fetch_credential_for_job(connection_id="c", job_id=job_id) as material:
        assert material.ruc == "20123456789"


def test_credential_error_response_oversized_never_retried_and_generic_code():
    from windows_agent.broker_client import CredentialFetchError

    calls = {"n": 0}

    def _handler(request):
        calls["n"] += 1
        return httpx.Response(500, content=b"y" * 5000)

    client, job_id = _client_with_transport(_handler)
    with pytest.raises(CredentialFetchError) as excinfo:
        with client.fetch_credential_for_job(connection_id="c", job_id=job_id):
            pass
    assert excinfo.value.code == "CREDENTIAL_REQUEST_FAILED"
    assert calls["n"] == 1


# --- E2.1-B: auditoria de respuesta de credencial malformada / sobredimensionada ---

_SENTINEL = "SENTINEL-SOL-PASSWORD-9f31"


def _fetch_expecting_failure(client, job_id, exc_type):
    with pytest.raises(exc_type) as excinfo:
        with client.fetch_credential_for_job(connection_id="c", job_id=job_id):
            pass
    return excinfo.value


def _assert_no_secret_anywhere(exc, caplog):
    assert _SENTINEL not in str(exc) and _SENTINEL not in repr(exc)
    assert _SENTINEL not in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        b'{"ruc": "20123456789", "sol_username": "U", "sol_password": "' + _SENTINEL.encode() + b'"',  # JSON invalido
        b'{"protocol_version": 1, "ruc": "20123456789", "sol_username": "U"}',  # falta campo requerido
        b'{"protocol_version": 1, "ruc": "20123456789", "sol_username": "U", "sol_password": "'
        + _SENTINEL.encode()
        + b'", "extra": "x"}',  # campo inesperado
        b'["' + _SENTINEL.encode() + b'"]',  # estructura invalida (no es objeto)
        b"\xff\xfe" + _SENTINEL.encode(),  # no decodificable
    ],
)
def test_Y_malformed_credential_body_fails_closed_never_retried_never_leaked(body, caplog):
    from windows_agent.broker_client import BrokerProtocolError

    calls = {"n": 0}

    def _handler(request):
        calls["n"] += 1
        return httpx.Response(200, headers=_GOOD_HEADERS, content=body)

    client, job_id = _client_with_transport(_handler)
    with caplog.at_level("DEBUG"):
        exc = _fetch_expecting_failure(client, job_id, BrokerProtocolError)
    assert calls["n"] == 1  # sin reintento generico
    _assert_no_secret_anywhere(exc, caplog)


@pytest.mark.parametrize("missing", ["Cache-Control", "Pragma", "X-Content-Type-Options"])
def test_Y_missing_required_security_header_fails_closed(missing):
    from windows_agent.broker_client import BrokerProtocolError

    headers = {k: v for k, v in _GOOD_HEADERS.items() if k != missing}
    body = f'{{"protocol_version": 1, "ruc": "20123456789", "sol_username": "U", "sol_password": "{_SENTINEL}"}}'
    client, job_id = _client_with_transport(lambda request: httpx.Response(200, headers=headers, content=body.encode()))
    exc = _fetch_expecting_failure(client, job_id, BrokerProtocolError)
    assert _SENTINEL not in str(exc) and _SENTINEL not in repr(exc)


def test_Z_lying_content_length_does_not_prevent_streaming_abort(caplog):
    """Content-Length declarado PEQUENO pero cuerpo real enorme: el tope se
    aplica a los bytes realmente acumulados, nunca confia en el header."""
    from windows_agent.broker_client import MAX_CREDENTIAL_RESPONSE_BYTES, BrokerProtocolError

    consumed = {"bytes": 0}

    def _endless():
        while True:
            consumed["bytes"] += 100
            yield (_SENTINEL * 5).encode()[:100]

    calls = {"n": 0}

    def _handler(request):
        calls["n"] += 1
        return httpx.Response(200, headers={**_GOOD_HEADERS, "Content-Length": "10"}, content=_endless())

    client, job_id = _client_with_transport(_handler)
    with caplog.at_level("DEBUG"):
        exc = _fetch_expecting_failure(client, job_id, BrokerProtocolError)
    assert consumed["bytes"] <= MAX_CREDENTIAL_RESPONSE_BYTES + 200
    assert calls["n"] == 1
    _assert_no_secret_anywhere(exc, caplog)


def test_Z_declared_oversized_content_length_rejected_before_any_body_read():
    from windows_agent.broker_client import BrokerProtocolError

    consumed = {"bytes": 0}

    def _body():
        consumed["bytes"] += 1
        yield b"{}"

    client, job_id = _client_with_transport(
        lambda request: httpx.Response(200, headers={**_GOOD_HEADERS, "Content-Length": "1025"}, content=_body())
    )
    _fetch_expecting_failure(client, job_id, BrokerProtocolError)
    assert consumed["bytes"] == 0  # el cuerpo nunca se empezo a leer


def test_Z_exactly_1024_bytes_is_accepted_and_1025_rejected():
    import json as _json

    from windows_agent.broker_client import BrokerProtocolError

    base = {"protocol_version": 1, "ruc": "20123456789", "sol_username": "U", "sol_password": ""}
    overhead = len(_json.dumps(base))
    exact = _json.dumps({**base, "sol_password": "p" * (1024 - overhead)}).encode()
    assert len(exact) == 1024
    client, job_id = _client_with_transport(lambda request: httpx.Response(200, headers=_GOOD_HEADERS, content=exact))
    with client.fetch_credential_for_job(connection_id="c", job_id=job_id) as material:
        assert material.sol_username == "U"

    over = _json.dumps({**base, "sol_password": "p" * (1025 - overhead)}).encode()
    assert len(over) == 1025
    client2, job_id2 = _client_with_transport(lambda request: httpx.Response(200, headers=_GOOD_HEADERS, content=over))
    _fetch_expecting_failure(client2, job_id2, BrokerProtocolError)


def test_credential_request_never_buffers_unbounded_body():
    """Guardia (AST, ignora comentarios) contra regresion a `response.content`/
    `.read()`/`.text`/`.json()` dentro de `_request_credential_once`."""
    import ast
    import inspect

    from windows_agent.broker_client import OperationalBrokerClient

    tree = ast.parse(inspect.getsource(OperationalBrokerClient._request_credential_once).lstrip())
    forbidden = {"content", "text", "read", "json", "aread"}
    offenders = [
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in forbidden and isinstance(node.value, ast.Name)
        and node.value.id == "response"
    ]
    assert offenders == []
