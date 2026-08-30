"""Fase 5, Bloque 5.2 (Domain C, ver docs/phase5/block5_2/, encargo seccion
60): concurrencia real de la CAPA DE SERVICIOS con DOS conexiones
PostgreSQL independientes -- mismo patron que
test_sol_agent_access_concurrency.py (Domain B) y
test_tax_payment_concurrency.py. Nunca SQLite, nunca mock, nunca solo
`sleep` como sustituto de sincronizacion real."""

from __future__ import annotations

import base64
import os
import threading
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_agent_access import SolAccessSession, SolAgent, SolAgentJob, SolAgentPairing
from app.models.sol_vault import SolVault, SolVaultDataKey
from app.models.tenant import Tenant
from app.models.user import User
from app.services import sol_agent_job_service as job_service
from app.services import sol_access_session_service as session_service
from app.services import sol_agent_pairing_service as pairing_service
from app.services.ruc_validator import compute_check_digit


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, future=True)


@pytest.fixture()
def pairing_key(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(get_settings(), "pairing_verifier_key", key, raising=False)
    # Domain C.1a, seccion 12: la capa process-local de throttle de
    # verificacion NO debe interferir con la prueba de la invariante de BD
    # (dos confirmaciones/quintos-fallos concurrentes) -- se desactiva
    # explicitamente para estas pruebas, que ya controlan su propia
    # sincronizacion via eventos/locks reales, sin sleeps arbitrarios.
    monkeypatch.setattr(get_settings(), "pairing_verification_throttle_seconds", 0.0, raising=False)
    return key


def _fake_ruc() -> str:
    base8 = f"{uuid.uuid4().int % 10**8:08d}"
    first_ten = "20" + base8
    return first_ten + str(compute_check_digit(first_ten))


def _commit_tenant_user(session_factory) -> dict:
    session = session_factory()
    try:
        tenant = Tenant(name="Tenant concurrencia servicios SOL")
        session.add(tenant)
        session.flush()
        admin = User(
            tenant_id=tenant.id,
            email=f"admin-svcconc-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Admin concurrencia servicios",
            password_hash=hash_password("TestPassword123!"),
            role="admin",
            is_active=True,
        )
        session.add(admin)
        session.flush()
        session.commit()
        return {"tenant_id": tenant.id, "admin_id": admin.id}
    finally:
        session.close()


def _commit_credential(session_factory, tenant_id, admin_id):
    session = session_factory()
    try:
        client = Client(
            tenant_id=tenant_id,
            ruc=_fake_ruc(),
            business_name="Cliente concurrencia servicios SOL",
            person_type="juridica",
            ruc_status="ACTIVO",
            fiscal_address_condition="HABIDO",
            tax_regime="GENERAL",
            service_status="activo",
        )
        session.add(client)
        session.flush()

        now = datetime.now(UTC)
        vault = session.query(SolVault).filter(SolVault.tenant_id == tenant_id).one_or_none()
        if vault is None:
            vault = SolVault(tenant_id=tenant_id, initialized_at=now, initialized_by=admin_id)
            session.add(vault)
            session.flush()
            data_key = SolVaultDataKey(
                tenant_id=tenant_id,
                vault_id=vault.id,
                data_key_version=1,
                wrapped_data_key=b"dummy-wrapped-data-key-not-real",
                activated_at=now,
                created_by=admin_id,
                created_at=now,
            )
            session.add(data_key)
            session.flush()

        credential = ClientSolCredential(
            tenant_id=tenant_id,
            client_id=client.id,
            vault_id=vault.id,
            credential_kind="SECONDARY",
            sol_username_encrypted=b"dummy-encrypted-username",
            sol_password_encrypted=b"dummy-encrypted-password",
            data_key_version=1,
            authorization_basis="Autorizacion de prueba (concurrencia servicios)",
            authorized_at=now,
            created_by=admin_id,
        )
        session.add(credential)
        session.flush()
        session.commit()
        return client.id, credential.id
    finally:
        session.close()


def _commit_agent(session_factory, tenant_id):
    session = session_factory()
    try:
        agent = SolAgent(
            tenant_id=tenant_id,
            display_name="Agente concurrencia servicios",
            certificate_fingerprint=f"fp-svc-{uuid.uuid4().hex}",
            paired_at=datetime.now(UTC),
        )
        session.add(agent)
        session.flush()
        session.commit()
        return agent.id
    finally:
        session.close()


def _cleanup(session_factory, tenant_id: uuid.UUID) -> None:
    session = session_factory()
    try:
        session.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        session.execute(delete(SolAgentJob).where(SolAgentJob.tenant_id == tenant_id))
        session.execute(delete(SolAgentPairing).where(SolAgentPairing.tenant_id == tenant_id))
        session.execute(delete(SolAccessSession).where(SolAccessSession.tenant_id == tenant_id))
        session.execute(delete(SolAgent).where(SolAgent.tenant_id == tenant_id))
        session.execute(delete(ClientSolCredential).where(ClientSolCredential.tenant_id == tenant_id))
        session.execute(delete(SolVaultDataKey).where(SolVaultDataKey.tenant_id == tenant_id))
        session.execute(delete(SolVault).where(SolVault.tenant_id == tenant_id))
        session.execute(delete(Client).where(Client.tenant_id == tenant_id))
        session.execute(delete(User).where(User.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()
    finally:
        session.close()


def _run_two(worker_a, worker_b, *, gate_event: threading.Event) -> None:
    thread_a = threading.Thread(target=worker_a)
    thread_b = threading.Thread(target=worker_b)
    thread_a.start()
    thread_b.start()
    assert gate_event.wait(timeout=5), "el segundo hilo nunca comenzo su intento"
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)


# --- A: idempotencia, mismo payload -> misma sesion logica ---


def _install_pre_insert_handoff(monkeypatch):
    """`create_session` no bloquea ninguna fila antes de insertar (no hay
    fila previa que bloquear -- la propia sesion es lo que se crea), asi
    que un simple `threading.Event` de arranque no basta para forzar la
    carrera real de INSERT: el primer hilo, sin ningun punto de espera
    intermedio, normalmente termina TODA su llamada (incluido el commit)
    antes de que el segundo hilo alcance su propia comprobacion. Se envuelve
    `get_active_session_for_credential` (la ULTIMA comprobacion antes del
    INSERT) para que el PRIMER hilo en llegar quede esperando a que el
    SEGUNDO tambien llegue al mismo punto -- momento en que ambos ya
    confirmaron "no hay sesion activa todavia" y ambos proceden a intentar
    el INSERT casi al mismo tiempo, dejando que el constraint unico de
    Postgres (no un mock) decida quien gana. Mismo espiritu que
    `_install_lock_handoff` en test_operational_task_concurrency.py, adaptado
    a una carrera de insercion en vez de una carrera de `SELECT ... FOR
    UPDATE`."""
    original = session_service.repo.get_active_session_for_credential
    state = {"first_done": False}
    guard = threading.Lock()
    first_reached = threading.Event()
    second_reached = threading.Event()

    def wrapped(db, **kwargs):
        result = original(db, **kwargs)
        with guard:
            is_first = not state["first_done"]
            state["first_done"] = True
        if is_first:
            first_reached.set()
            assert second_reached.wait(timeout=5), "el segundo intento nunca llego al mismo punto"
        else:
            second_reached.set()
        return result

    monkeypatch.setattr(session_service.repo, "get_active_session_for_credential", wrapped)


def test_A_same_idempotency_key_same_payload_one_logical_session(session_factory, monkeypatch):
    setup = _commit_tenant_user(session_factory)
    client_id, credential_id = _commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    key = uuid.uuid4()
    results: dict[str, uuid.UUID] = {}
    errors: dict[str, BaseException] = {}
    _install_pre_insert_handoff(monkeypatch)

    def worker(name):
        session = session_factory()
        try:
            row = session_service.create_session(
                session,
                tenant_id=setup["tenant_id"],
                client_id=client_id,
                credential_id=credential_id,
                requested_by_user_id=setup["admin_id"],
                idempotency_key=key,
            )
            session.commit()
            results[name] = row.id
        except BaseException as exc:  # noqa: BLE001
            errors[name] = exc
            session.rollback()
        finally:
            session.close()

    try:
        thread_a = threading.Thread(target=worker, args=("a",))
        thread_b = threading.Thread(target=worker, args=("b",))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)

        assert not errors, f"no se esperaban errores: {errors}"
        assert results["a"] == results["b"], "ambos deben resolver a la MISMA sesion logica"
    finally:
        _cleanup(session_factory, setup["tenant_id"])


# --- B: idempotencia, mismo key/payload distinto -> conflicto deterministico ---


def test_B_same_idempotency_key_different_payload_deterministic_conflict(session_factory):
    setup = _commit_tenant_user(session_factory)
    client_a, credential_a = _commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    client_b, credential_b = _commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    key = uuid.uuid4()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    a_started = threading.Event()
    b_started = threading.Event()

    def worker(name, client_id, credential_id, started_event, other_event):
        session = session_factory()
        try:
            started_event.set()
            other_event.wait(timeout=5)
            row = session_service.create_session(
                session,
                tenant_id=setup["tenant_id"],
                client_id=client_id,
                credential_id=credential_id,
                requested_by_user_id=setup["admin_id"],
                idempotency_key=key,
            )
            session.commit()
            results[name] = row.id
        except BaseException as exc:  # noqa: BLE001
            errors[name] = exc
            session.rollback()
        finally:
            session.close()

    try:
        _run_two(
            lambda: worker("a", client_a, credential_a, a_started, b_started),
            lambda: worker("b", client_b, credential_b, b_started, a_started),
            gate_event=b_started,
        )
        succeeded = [k for k in ("a", "b") if k in results]
        failed = [k for k in ("a", "b") if k in errors]
        assert len(succeeded) == 1, f"se esperaba exactamente 1 exito: {results} / {errors}"
        assert len(failed) == 1
        assert isinstance(errors[failed[0]], session_service.IdempotencyKeyConflictError)
    finally:
        _cleanup(session_factory, setup["tenant_id"])


# --- C: dos confirmaciones de pairing simultaneas -> a lo sumo un agente ---


def test_C_two_pairing_confirmations_at_most_one_agent(session_factory, pairing_key):
    setup = _commit_tenant_user(session_factory)
    session = session_factory()
    try:
        result = pairing_service.create_pairing(
            session,
            tenant_id=setup["tenant_id"],
            requested_by_user_id=setup["admin_id"],
            agent_display_name_candidate="Agente concurrente",
        )
        session.commit()
        pairing_id = result.pairing.id
        code = result.pairing_code
    finally:
        session.close()

    a_locked = threading.Event()
    b_attempting = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def worker_a():
        session = session_factory()
        try:
            pairing_service.confirm_pairing(
                session,
                tenant_id=setup["tenant_id"],
                pairing_id=pairing_id,
                code=code,
                issue_certificate=lambda: f"fp-a-{uuid.uuid4().hex}",
                actor_user_id=setup["admin_id"],
            )
            a_locked.set()
            b_attempting.wait(timeout=5)
            import time

            time.sleep(0.3)
            session.commit()
            results["a"] = "completed"
        except BaseException as exc:  # noqa: BLE001
            errors["a"] = exc
            session.rollback()
        finally:
            session.close()

    def worker_b():
        a_locked.wait(timeout=5)
        session = session_factory()
        try:
            b_attempting.set()
            # Bloquea de verdad contra el `SELECT ... FOR UPDATE` de A hasta
            # que A resuelva su transaccion.
            pairing_service.confirm_pairing(
                session,
                tenant_id=setup["tenant_id"],
                pairing_id=pairing_id,
                code=code,
                issue_certificate=lambda: f"fp-b-{uuid.uuid4().hex}",
                actor_user_id=setup["admin_id"],
            )
            session.commit()
            results["b"] = "completed"
        except BaseException as exc:  # noqa: BLE001
            errors["b"] = exc
            session.rollback()
        finally:
            session.close()

    try:
        thread_a = threading.Thread(target=worker_a)
        thread_b = threading.Thread(target=worker_b)
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)

        succeeded = [k for k in ("a", "b") if k in results]
        failed = [k for k in ("a", "b") if k in errors]
        assert len(succeeded) == 1, f"se esperaba exactamente 1 completado: {results} / {errors}"
        assert len(failed) == 1
        assert isinstance(errors[failed[0]], pairing_service.PairingNotPendingError)

        verify = session_factory()
        try:
            agents = verify.query(SolAgent).filter(SolAgent.tenant_id == setup["tenant_id"]).all()
            assert len(agents) == 1, "a lo sumo un agente debe haberse creado"
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, setup["tenant_id"])


# --- D: dos intentos que compiten por ser el quinto fallo -> LOCKED durable, sin corrupcion ---


def test_D_two_fifth_failure_attempts_durable_locked_no_counter_corruption(session_factory, pairing_key):
    setup = _commit_tenant_user(session_factory)
    session = session_factory()
    try:
        result = pairing_service.create_pairing(
            session,
            tenant_id=setup["tenant_id"],
            requested_by_user_id=setup["admin_id"],
            agent_display_name_candidate="Agente concurrente D",
        )
        session.commit()
        pairing_id = result.pairing.id
        wrong_code = "000000" if result.pairing_code != "000000" else "111111"

        for _ in range(4):
            try:
                pairing_service.confirm_pairing(
                    session,
                    tenant_id=setup["tenant_id"],
                    pairing_id=pairing_id,
                    code=wrong_code,
                    issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
                    actor_user_id=setup["admin_id"],
                )
            except pairing_service.PairingInvalidCodeError:
                session.commit()
    finally:
        session.close()

    a_locked = threading.Event()
    b_attempting = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def worker(name, locked_event, other_event, wait_for_other):
        session = session_factory()
        try:
            if wait_for_other:
                other_event.wait(timeout=5)
            try:
                pairing_service.confirm_pairing(
                    session,
                    tenant_id=setup["tenant_id"],
                    pairing_id=pairing_id,
                    code=wrong_code,
                    issue_certificate=lambda: f"fp-{uuid.uuid4().hex}",
                    actor_user_id=setup["admin_id"],
                )
                results[name] = "no_error"
            except pairing_service.PairingLockedError:
                results[name] = "locked"
            finally:
                locked_event.set()
                if not wait_for_other:
                    import time

                    time.sleep(0.3)
                session.commit()
        except BaseException as exc:  # noqa: BLE001
            errors[name] = exc
            session.rollback()
        finally:
            session.close()

    try:
        thread_a = threading.Thread(target=worker, args=("a", a_locked, b_attempting, False))
        thread_b = threading.Thread(target=worker, args=("b", b_attempting, a_locked, True))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)

        assert not errors, f"no se esperaban errores no manejados: {errors}"
        assert results.get("a") == "locked" or results.get("b") == "locked"

        verify = session_factory()
        try:
            pairing = verify.get(SolAgentPairing, pairing_id)
            assert pairing.status == "LOCKED"
            assert pairing.failed_attempt_count == 5, f"conteo corrupto: {pairing.failed_attempt_count}"
            assert pairing.pairing_code_verifier is None
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, setup["tenant_id"])


# --- E: dos claims del mismo job -> exactamente uno exitoso ---


def test_E_two_claims_same_job_exactly_one_succeeds(session_factory):
    setup = _commit_tenant_user(session_factory)
    client_id, credential_id = _commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    agent_id = _commit_agent(session_factory, setup["tenant_id"])

    session = session_factory()
    try:
        session_row = session_service.create_session(
            session,
            tenant_id=setup["tenant_id"],
            client_id=client_id,
            credential_id=credential_id,
            requested_by_user_id=setup["admin_id"],
            idempotency_key=uuid.uuid4(),
        )
        job_result = job_service.create_job(
            session, tenant_id=setup["tenant_id"], session_id=session_row.id, agent_id=agent_id
        )
        session.commit()
        token = job_result.job_token
    finally:
        session.close()

    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    a_started = threading.Event()
    b_started = threading.Event()

    def worker(name, started_event, other_event):
        session = session_factory()
        try:
            started_event.set()
            other_event.wait(timeout=5)
            job = job_service.claim_job(session, job_token=token)
            session.commit()
            results[name] = job.id
        except BaseException as exc:  # noqa: BLE001
            errors[name] = exc
            session.rollback()
        finally:
            session.close()

    try:
        _run_two(
            lambda: worker("a", a_started, b_started),
            lambda: worker("b", b_started, a_started),
            gate_event=b_started,
        )
        succeeded = [k for k in ("a", "b") if k in results]
        failed = [k for k in ("a", "b") if k in errors]
        assert len(succeeded) == 1, f"se esperaba exactamente 1 claim exitoso: {results} / {errors}"
        assert len(failed) == 1
        assert isinstance(errors[failed[0]], job_service.JobClaimInvalidError)
    finally:
        _cleanup(session_factory, setup["tenant_id"])


# --- F: cancel vs claim -> resultado deterministico ---


def test_F_cancel_vs_claim_deterministic(session_factory):
    setup = _commit_tenant_user(session_factory)
    client_id, credential_id = _commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    agent_id = _commit_agent(session_factory, setup["tenant_id"])

    session = session_factory()
    try:
        session_row = session_service.create_session(
            session,
            tenant_id=setup["tenant_id"],
            client_id=client_id,
            credential_id=credential_id,
            requested_by_user_id=setup["admin_id"],
            idempotency_key=uuid.uuid4(),
        )
        job_result = job_service.create_job(
            session, tenant_id=setup["tenant_id"], session_id=session_row.id, agent_id=agent_id
        )
        session.commit()
        session_id = session_row.id
        token = job_result.job_token
    finally:
        session.close()

    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    a_started = threading.Event()
    b_started = threading.Event()

    def worker_cancel():
        session = session_factory()
        try:
            a_started.set()
            b_started.wait(timeout=5)
            import time

            time.sleep(0.2)
            row = session_service.cancel_session(session, tenant_id=setup["tenant_id"], session_id=session_id)
            session.commit()
            results["cancel"] = row.status
        except BaseException as exc:  # noqa: BLE001
            errors["cancel"] = exc
            session.rollback()
        finally:
            session.close()

    def worker_claim():
        a_started.wait(timeout=5)
        session = session_factory()
        try:
            b_started.set()
            job = job_service.claim_job(session, job_token=token)
            session.commit()
            results["claim"] = job.status
        except BaseException as exc:  # noqa: BLE001
            errors["claim"] = exc
            session.rollback()
        finally:
            session.close()

    try:
        thread_cancel = threading.Thread(target=worker_cancel)
        thread_claim = threading.Thread(target=worker_claim)
        thread_cancel.start()
        thread_claim.start()
        thread_cancel.join(timeout=15)
        thread_claim.join(timeout=15)

        assert "cancel" in results, f"la cancelacion nunca debe fallar: {errors}"
        # El claim gana (corre primero, sesion aun no cancelada) o pierde
        # (encuentra la sesion ya cancelada) -- cualquiera de los dos es un
        # resultado deterministico valido, nunca ambos "ganan" de forma
        # inconsistente.
        assert ("claim" in results) != ("claim" in errors)

        verify = session_factory()
        try:
            job = verify.query(SolAgentJob).filter(SolAgentJob.session_id == session_id).one()
            # `cancel_session` cancela el job activo de la sesion sin
            # importar si estaba PENDING o ya CLAIMED (JOB_ACTIVE_STATUSES
            # incluye ambos) -- por eso el resultado deterministico final es
            # SIEMPRE CANCELLED en cuanto cancel_session realmente corre (lo
            # hace en ambos ordenes de esta carrera), incluso si claim_job
            # "gano" transitoriamente y devolvio CLAIMED en su propio resultado.
            assert job.status == "CANCELLED"
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, setup["tenant_id"])


# --- G: cancel vs exito de sesion -> terminal no se sobreescribe ---


def test_G_cancel_vs_success_terminal_wins_once(session_factory):
    setup = _commit_tenant_user(session_factory)
    client_id, credential_id = _commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    agent_id = _commit_agent(session_factory, setup["tenant_id"])

    session = session_factory()
    try:
        session_row = session_service.create_session(
            session,
            tenant_id=setup["tenant_id"],
            client_id=client_id,
            credential_id=credential_id,
            requested_by_user_id=setup["admin_id"],
            idempotency_key=uuid.uuid4(),
        )
        job_service.create_job(session, tenant_id=setup["tenant_id"], session_id=session_row.id, agent_id=agent_id)
        session_row = session_service.start_progress(
            session, tenant_id=setup["tenant_id"], session_id=session_row.id, expected_version=session_row.version
        )
        session.commit()
        session_id = session_row.id
        expected_version = session_row.version
    finally:
        session.close()

    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    a_locked = threading.Event()
    b_attempting = threading.Event()

    def worker_cancel():
        session = session_factory()
        try:
            row = session_service.cancel_session(session, tenant_id=setup["tenant_id"], session_id=session_id)
            a_locked.set()
            b_attempting.wait(timeout=5)
            import time

            time.sleep(0.3)
            session.commit()
            results["cancel"] = row.status
        except BaseException as exc:  # noqa: BLE001
            errors["cancel"] = exc
            session.rollback()
        finally:
            session.close()

    def worker_succeed():
        a_locked.wait(timeout=5)
        session = session_factory()
        try:
            b_attempting.set()
            row = session_service.report_succeeded(
                session, tenant_id=setup["tenant_id"], session_id=session_id, expected_version=expected_version
            )
            session.commit()
            results["succeed"] = row.status
        except BaseException as exc:  # noqa: BLE001
            errors["succeed"] = exc
            session.rollback()
        finally:
            session.close()

    try:
        thread_cancel = threading.Thread(target=worker_cancel)
        thread_succeed = threading.Thread(target=worker_succeed)
        thread_cancel.start()
        thread_succeed.start()
        thread_cancel.join(timeout=15)
        thread_succeed.join(timeout=15)

        assert "cancel" in results, f"cancel debe ganar la carrera (corrio primero): {errors}"
        assert "succeed" in errors, f"succeed debe fallar tras cancel: {results}"
        # `report_succeeded` capturo `expected_version` ANTES de que
        # `cancel_session` corriera -- por construccion, en cuanto cancel
        # comitea primero (incrementando version), el rechazo llega via la
        # verificacion de version (mas generica, y verificada PRIMERO en
        # `_transition_locked`), no via `SessionTerminalError` -- ambos son
        # rechazos correctos que impiden la sobreescritura del terminal;
        # `SessionTerminalError` es para el caso distinto de un llamador que
        # SI conoce la version actual (terminal) e insiste igual.
        assert isinstance(errors["succeed"], session_service.SessionVersionConflictError)

        verify = session_factory()
        try:
            row = session_service.get_session(verify, tenant_id=setup["tenant_id"], session_id=session_id)
            assert row.status == "CANCELLED", "el primer terminal comprometido debe prevalecer, sin sobreescritura"
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, setup["tenant_id"])


# --- H: expire vs succeed (IN_PROGRESS) -> primer terminal comprometido gana ---
#
# Domain C.1, Gate 1: `IN_PROGRESS -> EXPIRED` fue corregido en la matriz
# congelada (BLOCK5_2_STATE_MACHINE.md seccion 2 -- antes ausente, ver el
# informe de Domain C.1) precisamente para que esta carrera sea real y
# alcanzable, en vez de sustituida por otra. Se fuerza a `expire_session`
# a ganar (usando el mismo protocolo de handoff por eventos que las demas
# pruebas de esta matriz); el caso simetrico ("SUCCEEDED gana, EXPIRED
# posterior no modifica el terminal") queda cubierto por el MISMO mecanismo
# de bloqueo de fila + `expected_version` + comprobacion de estado terminal
# ya probado exhaustivamente en C/D/E/F/G -- no es una ruta de codigo
# distinta seguida solo por este caso.


def test_H_expire_vs_success_real_race_first_committed_terminal_wins(session_factory):
    setup = _commit_tenant_user(session_factory)
    client_id, credential_id = _commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    agent_id = _commit_agent(session_factory, setup["tenant_id"])

    session = session_factory()
    try:
        session_row = session_service.create_session(
            session,
            tenant_id=setup["tenant_id"],
            client_id=client_id,
            credential_id=credential_id,
            requested_by_user_id=setup["admin_id"],
            idempotency_key=uuid.uuid4(),
        )
        job_service.create_job(session, tenant_id=setup["tenant_id"], session_id=session_row.id, agent_id=agent_id)
        session_row = session_service.start_progress(
            session, tenant_id=setup["tenant_id"], session_id=session_row.id, expected_version=session_row.version
        )
        assert session_row.status == "IN_PROGRESS"
        session_row.expires_at = datetime(2020, 1, 1, tzinfo=UTC)
        session.flush()
        session.commit()
        session_id = session_row.id
        expected_version = session_row.version
    finally:
        session.close()

    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    a_locked = threading.Event()
    b_attempting = threading.Event()

    def worker_expire():
        session = session_factory()
        try:
            row = session_service.expire_session(session, tenant_id=setup["tenant_id"], session_id=session_id)
            a_locked.set()
            b_attempting.wait(timeout=5)
            import time

            time.sleep(0.3)
            session.commit()
            results["expire"] = row.status
        except BaseException as exc:  # noqa: BLE001
            errors["expire"] = exc
            session.rollback()
        finally:
            session.close()

    def worker_succeed():
        a_locked.wait(timeout=5)
        session = session_factory()
        try:
            b_attempting.set()
            row = session_service.report_succeeded(
                session, tenant_id=setup["tenant_id"], session_id=session_id, expected_version=expected_version
            )
            session.commit()
            results["succeed"] = row.status
        except BaseException as exc:  # noqa: BLE001
            errors["succeed"] = exc
            session.rollback()
        finally:
            session.close()

    try:
        thread_expire = threading.Thread(target=worker_expire)
        thread_succeed = threading.Thread(target=worker_succeed)
        thread_expire.start()
        thread_succeed.start()
        thread_expire.join(timeout=15)
        thread_succeed.join(timeout=15)

        assert "expire" in results, f"expire debe ganar la carrera (corrio primero): {errors}"
        assert "succeed" in errors, f"succeed debe fallar tras expire: {results}"
        assert isinstance(errors["succeed"], session_service.SessionVersionConflictError)

        verify = session_factory()
        try:
            row = session_service.get_session(verify, tenant_id=setup["tenant_id"], session_id=session_id)
            assert row.status == "EXPIRED", "el primer terminal comprometido debe prevalecer, sin sobreescritura"
        finally:
            verify.close()
        print("EXPIRE_VS_SUCCESS_RACE=PASS")
    finally:
        _cleanup(session_factory, setup["tenant_id"])


# --- I: dos intentos de entrega de credencial sobre el MISMO job -> exactamente uno exitoso ---
#
# Nota de diseno: dos jobs DISTINTOS de la misma sesion nunca pueden estar
# ambos CLAIMED simultaneamente (el indice unico parcial
# `ux_sol_agent_jobs_active_per_session` ya lo impide -- probado con SQL
# crudo de dos conexiones en Domain B, test_sol_agent_access_concurrency.py).
# `mark_credential_delivered` exige `status == 'CLAIMED'`, asi que al nivel
# de SERVICIO la carrera real y honesta es sobre el MISMO job: dos llamadas
# concurrentes deben resolver a exactamente un ganador (bloqueo de fila +
# `expected_version`), nunca ambas.


def test_I_two_credential_delivery_gate_attempts_same_job_exactly_one_succeeds(session_factory):
    setup = _commit_tenant_user(session_factory)
    client_id, credential_id = _commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    agent_id = _commit_agent(session_factory, setup["tenant_id"])

    session = session_factory()
    try:
        session_row = session_service.create_session(
            session,
            tenant_id=setup["tenant_id"],
            client_id=client_id,
            credential_id=credential_id,
            requested_by_user_id=setup["admin_id"],
            idempotency_key=uuid.uuid4(),
        )
        job_result = job_service.create_job(
            session, tenant_id=setup["tenant_id"], session_id=session_row.id, agent_id=agent_id
        )
        session.commit()
        token = job_result.job_token
    finally:
        session.close()

    session = session_factory()
    try:
        claimed = job_service.claim_job(session, job_token=token)
        session.commit()
        job_id = claimed.id
        expected_version = claimed.version
    finally:
        session.close()

    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    a_locked = threading.Event()
    b_attempting = threading.Event()

    def worker_a():
        session = session_factory()
        try:
            job = job_service.mark_credential_delivered(
                session, tenant_id=setup["tenant_id"], job_id=job_id, expected_version=expected_version
            )
            a_locked.set()
            b_attempting.wait(timeout=5)
            import time

            time.sleep(0.3)
            session.commit()
            results["a"] = job.credential_delivered_at is not None
        except BaseException as exc:  # noqa: BLE001
            errors["a"] = exc
            session.rollback()
        finally:
            session.close()

    def worker_b():
        a_locked.wait(timeout=5)
        session = session_factory()
        try:
            b_attempting.set()
            # Bloquea de verdad contra el `SELECT ... FOR UPDATE` de A hasta
            # que A resuelva su transaccion.
            job = job_service.mark_credential_delivered(
                session, tenant_id=setup["tenant_id"], job_id=job_id, expected_version=expected_version
            )
            session.commit()
            results["b"] = job.credential_delivered_at is not None
        except BaseException as exc:  # noqa: BLE001
            errors["b"] = exc
            session.rollback()
        finally:
            session.close()

    try:
        thread_a = threading.Thread(target=worker_a)
        thread_b = threading.Thread(target=worker_b)
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=15)
        thread_b.join(timeout=15)

        succeeded = [k for k in ("a", "b") if k in results]
        failed = [k for k in ("a", "b") if k in errors]
        assert len(succeeded) == 1, f"se esperaba exactamente 1 entrega exitosa: {results} / {errors}"
        assert len(failed) == 1
        assert isinstance(errors[failed[0]], job_service.JobVersionConflictError)

        verify = session_factory()
        try:
            job = job_service.get_job(verify, tenant_id=setup["tenant_id"], job_id=job_id)
            assert job.credential_delivered_at is not None
        finally:
            verify.close()
        print("TWO_CONNECTION_DELIVERY_GATE_RACE=PASS")
    finally:
        _cleanup(session_factory, setup["tenant_id"])
