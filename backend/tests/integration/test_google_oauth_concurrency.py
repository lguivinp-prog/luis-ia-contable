"""Concurrencia real de la integracion Google OAuth (Fase 3 / Bloque 3.1,
Seccion 21 del plan v3): dos conexiones PostgreSQL genuinamente
independientes, con `threading.Event` para forzar el bloqueo real de fila
-- nunca un `sleep` como sustituto. Mismo patron que
test_operational_task_concurrency.py."""

import base64
import os
import threading
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.domain.google_oauth.fake_provider import FakeAuthorizationCode, FakeOAuthProvider
from app.models.audit_log import AuditLog
from app.models.google_oauth import GoogleOAuthAuthorizationAttempt, GoogleOAuthConnection
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories import google_oauth_repository as repo
from app.services import google_oauth_service as svc

_REDIRECT_URI = "http://localhost:8000/api/v1/integrations/google/callback"


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, future=True)


@pytest.fixture(autouse=True)
def _configured_encryption_key(monkeypatch):
    from app.core import integration_crypto

    settings = integration_crypto.get_settings()
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(settings, "integration_token_encryption_key", key, raising=False)
    monkeypatch.setattr(settings, "integration_token_key_id", "v1", raising=False)


def _commit_tenant_with_users(session_factory, count: int) -> dict:
    session = session_factory()
    try:
        tenant = Tenant(name="Tenant concurrencia OAuth")
        session.add(tenant)
        session.flush()
        users = []
        for i in range(count):
            user = User(
                tenant_id=tenant.id,
                email=f"oauth-conc-{i}-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
                full_name=f"Usuario concurrencia {i}",
                password_hash=hash_password("TestPassword123!"),
                role="admin",
                is_active=True,
            )
            session.add(user)
            users.append(user)
        session.flush()
        session.commit()
        return {"tenant_id": tenant.id, "user_ids": [u.id for u in users]}
    finally:
        session.close()


def _cleanup(session_factory, tenant_id: uuid.UUID) -> None:
    """Borra todas las filas comiteadas por esta prueba -- estas pruebas
    usan `session_factory` (conexiones reales, commit real), a diferencia
    de `db_session` (savepoint que se revierte solo). Sin esta limpieza, las
    filas persistirian en la base de datos de pruebas compartida y
    contaminarian otras pruebas que escanean tablas sin filtrar por tenant
    (p. ej. `select(AuditLog)` sin WHERE)."""
    session = session_factory()
    try:
        session.execute(
            delete(GoogleOAuthAuthorizationAttempt).where(
                GoogleOAuthAuthorizationAttempt.tenant_id == tenant_id
            )
        )
        session.execute(
            delete(GoogleOAuthConnection).where(GoogleOAuthConnection.tenant_id == tenant_id)
        )
        session.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        session.execute(delete(User).where(User.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()
    finally:
        session.close()


def test_double_callback_same_state_only_one_succeeds(session_factory) -> None:
    """Dos peticiones de callback con el MISMO `state` -- p. ej. el
    navegador reintenta la peticion, o el usuario recarga la pagina de
    retorno -- deben terminar con exactamente una conexion creada, nunca
    dos, y la segunda debe recibir INVALID_STATE."""
    setup = _commit_tenant_with_users(session_factory, 1)
    tenant_id = setup["tenant_id"]
    user_id = setup["user_ids"][0]
    try:
        _scenario_double_callback(session_factory, tenant_id, user_id)
    finally:
        _cleanup(session_factory, tenant_id)


def _scenario_double_callback(session_factory, tenant_id, user_id) -> None:
    fake_provider = FakeOAuthProvider()
    prep_session = session_factory()
    try:
        authorization_url, _ = svc.start_authorization(
            prep_session,
            tenant_id=tenant_id,
            user_id=user_id,
            capability="IDENTITY",
            return_path="/settings/integrations/google",
            redirect_uri=_REDIRECT_URI,
            oauth_provider=fake_provider,
        )
        prep_session.commit()
    finally:
        prep_session.close()

    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(authorization_url).query)["state"][0]
    code = f"code-{state[:8]}"
    fake_provider.register_authorization_code(
        code, FakeAuthorizationCode(sub="sub-conc-1", email="conc1@example.com", scopes=("openid", "email"))
    )

    lock_acquired = threading.Event()
    b_started = threading.Event()
    allow_a_to_finish = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    session_a_holder: dict[str, object] = {}
    original_lock = repo.lock_attempt_by_state_hash

    def patched_lock(db, state_hash):
        row = original_lock(db, state_hash)
        if db is session_a_holder.get("session"):
            lock_acquired.set()
            allow_a_to_finish.wait(timeout=5)
        return row

    def worker_a():
        session = session_factory()
        session_a_holder["session"] = session
        try:
            results["a"] = svc.process_callback(
                session,
                current_tenant_id=tenant_id,
                current_user_id=user_id,
                state=state,
                code=code,
                error=None,
                redirect_uri=_REDIRECT_URI,
                oauth_provider=fake_provider,
            )
        except BaseException as exc:  # noqa: BLE001 -- reportado en el hilo principal
            errors["a"] = exc
        finally:
            session.close()

    def worker_b():
        session = session_factory()
        try:
            lock_acquired.wait(timeout=5)
            b_started.set()
            results["b"] = svc.process_callback(
                session,
                current_tenant_id=tenant_id,
                current_user_id=user_id,
                state=state,
                code=code,
                error=None,
                redirect_uri=_REDIRECT_URI,
                oauth_provider=fake_provider,
            )
        except BaseException as exc:  # noqa: BLE001 -- reportado en el hilo principal
            errors["b"] = exc
        finally:
            session.close()

    import app.repositories.google_oauth_repository as repo_module

    repo_module.lock_attempt_by_state_hash = patched_lock
    try:
        thread_a = threading.Thread(target=worker_a)
        thread_b = threading.Thread(target=worker_b)
        thread_a.start()
        thread_b.start()
        assert b_started.wait(timeout=5), "el hilo B nunca comenzo su operacion"
        allow_a_to_finish.set()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)
    finally:
        repo_module.lock_attempt_by_state_hash = original_lock

    assert "a" in results, f"el hilo A no completo exitosamente: {errors.get('a')}"
    assert results["a"].succeeded is True
    assert "b" in errors, "el hilo B deberia haber recibido InvalidStateError"
    assert isinstance(errors["b"], svc.InvalidStateError)

    verify_session = session_factory()
    try:
        connection = repo.get_connection(verify_session, tenant_id, user_id)
        assert connection is not None
        assert connection.status == "ACTIVE"
        assert connection.version == 1  # una sola conexion creada, no duplicada
    finally:
        verify_session.close()


def test_two_users_connecting_same_google_account_one_wins(session_factory) -> None:
    """Dos usuarios del mismo tenant intentando conectar la MISMA cuenta
    Google (mismo `sub`) de forma concurrente: uno de los dos debe fallar
    por la restriccion UNIQUE(tenant_id, google_account_id), verificado con
    dos conexiones reales genuinamente concurrentes."""
    setup = _commit_tenant_with_users(session_factory, 2)
    tenant_id = setup["tenant_id"]
    user_a_id, user_b_id = setup["user_ids"]
    try:
        _scenario_two_users_same_account(session_factory, tenant_id, user_a_id, user_b_id)
    finally:
        _cleanup(session_factory, tenant_id)


def _scenario_two_users_same_account(session_factory, tenant_id, user_a_id, user_b_id) -> None:
    fake_provider = FakeOAuthProvider()
    states = {}
    for label, uid in (("a", user_a_id), ("b", user_b_id)):
        prep_session = session_factory()
        try:
            url, _ = svc.start_authorization(
                prep_session,
                tenant_id=tenant_id,
                user_id=uid,
                capability="IDENTITY",
                return_path="/settings/integrations/google",
                redirect_uri=_REDIRECT_URI,
                oauth_provider=fake_provider,
            )
            prep_session.commit()
        finally:
            prep_session.close()
        from urllib.parse import parse_qs, urlparse

        states[label] = parse_qs(urlparse(url).query)["state"][0]

    code_a = f"code-{states['a'][:8]}"
    code_b = f"code-{states['b'][:8]}"
    fake_provider.register_authorization_code(
        code_a, FakeAuthorizationCode(sub="shared-sub", email="a@example.com", scopes=("openid", "email"))
    )
    fake_provider.register_authorization_code(
        code_b, FakeAuthorizationCode(sub="shared-sub", email="b@example.com", scopes=("openid", "email"))
    )

    connection_lock_acquired = threading.Event()
    b_started = threading.Event()
    allow_a_to_finish = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    session_a_holder: dict[str, object] = {}
    original_lock_conn = repo.lock_connection_for_update

    def patched_lock_conn(db, tenant_id_arg, user_id_arg):
        row = original_lock_conn(db, tenant_id_arg, user_id_arg)
        if db is session_a_holder.get("session"):
            connection_lock_acquired.set()
            allow_a_to_finish.wait(timeout=5)
        return row

    def worker_a():
        session = session_factory()
        session_a_holder["session"] = session
        try:
            results["a"] = svc.process_callback(
                session,
                current_tenant_id=tenant_id,
                current_user_id=user_a_id,
                state=states["a"],
                code=code_a,
                error=None,
                redirect_uri=_REDIRECT_URI,
                oauth_provider=fake_provider,
            )
        except BaseException as exc:  # noqa: BLE001
            errors["a"] = exc
        finally:
            session.close()

    def worker_b():
        session = session_factory()
        try:
            connection_lock_acquired.wait(timeout=5)
            b_started.set()
            results["b"] = svc.process_callback(
                session,
                current_tenant_id=tenant_id,
                current_user_id=user_b_id,
                state=states["b"],
                code=code_b,
                error=None,
                redirect_uri=_REDIRECT_URI,
                oauth_provider=fake_provider,
            )
        except BaseException as exc:  # noqa: BLE001
            errors["b"] = exc
        finally:
            session.close()

    import app.repositories.google_oauth_repository as repo_module

    repo_module.lock_connection_for_update = patched_lock_conn
    try:
        thread_a = threading.Thread(target=worker_a)
        thread_b = threading.Thread(target=worker_b)
        thread_a.start()
        thread_b.start()
        assert b_started.wait(timeout=5), "el hilo B nunca comenzo su operacion"
        allow_a_to_finish.set()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)
    finally:
        repo_module.lock_connection_for_update = original_lock_conn

    # A no bloqueaba realmente ninguna fila preexistente (su INSERT es
    # nuevo) -- lo que importa es que, al final, solo una de las dos
    # conexiones exista para la cuenta compartida.
    verify_session = session_factory()
    try:
        conn_a = repo.get_connection(verify_session, tenant_id, user_a_id)
        conn_b = repo.get_connection(verify_session, tenant_id, user_b_id)
        existing = [c for c in (conn_a, conn_b) if c is not None and c.status == "ACTIVE"]
        assert len(existing) == 1, "exactamente una de las dos conexiones debe quedar ACTIVE"
    finally:
        verify_session.close()

    outcomes_succeeded = [r for r in results.values() if getattr(r, "succeeded", False)]
    assert len(outcomes_succeeded) == 1
