"""Concurrencia real de borradores de comunicacion (Fase 3, Bloque 3.2, ver
ADR-0067): dos conexiones reales independientes contra el mismo
`test_engine`, con `threading.Event`/`Thread.join(timeout=...)` para forzar
sincronizacion real -- nunca un `sleep` -- mismo patron que
`test_operational_task_concurrency.py`."""

import threading
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.models.audit_log import AuditLog
from app.models.client import Client
from app.models.communication_draft import CommunicationDraft
from app.models.tenant import Tenant
from app.models.user import User
from app.services import communication_draft_service as svc
from app.services.ruc_validator import compute_check_digit


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, future=True)


def _fake_ruc() -> str:
    base8 = f"{uuid.uuid4().int % 10**8:08d}"
    first_ten = "20" + base8
    return first_ten + str(compute_check_digit(first_ten))


def _commit_tenant_user_client(session_factory) -> dict:
    session = session_factory()
    try:
        tenant = Tenant(name="Tenant concurrencia comunicaciones")
        session.add(tenant)
        session.flush()
        admin = User(
            tenant_id=tenant.id,
            email=f"admin-comm-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Admin concurrencia comunicaciones",
            password_hash=hash_password("TestPassword123!"),
            role="admin",
            is_active=True,
        )
        session.add(admin)
        session.flush()
        client_row = Client(
            tenant_id=tenant.id,
            ruc=_fake_ruc(),
            business_name="Cliente concurrencia comunicaciones",
            person_type="juridica",
            ruc_status="ACTIVO",
            fiscal_address_condition="HABIDO",
            tax_regime="GENERAL",
            service_status="activo",
            whatsapp_number="987654321",
        )
        session.add(client_row)
        session.flush()
        session.commit()
        return {"tenant_id": tenant.id, "admin_id": admin.id, "client_id": client_row.id}
    finally:
        session.close()


def _commit_draft(session_factory, tenant_id, admin_id, client_id) -> uuid.UUID:
    session = session_factory()
    try:
        admin = session.get(User, admin_id)
        draft = svc.create_draft(
            session,
            tenant_id=tenant_id,
            actor=admin,
            client_id=client_id,
            related_task_id=None,
            related_document_file_id=None,
            template_code=None,
            template_version=None,
            body="Mensaje de concurrencia de prueba",
            recipient_phone=None,
        )
        session.commit()
        return draft.id
    finally:
        session.close()


def _cleanup(session_factory, tenant_id: uuid.UUID) -> None:
    from sqlalchemy import delete

    from app.models.audit_log import AuditLog

    session = session_factory()
    try:
        session.execute(delete(CommunicationDraft).where(CommunicationDraft.tenant_id == tenant_id))
        session.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        session.execute(delete(Client).where(Client.tenant_id == tenant_id))
        session.execute(delete(User).where(User.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()
    finally:
        session.close()


def _run_two_actions_with_lock_handoff(session_factory, action_a, action_b):
    """Mismo patron de traspaso de lock que
    `test_operational_task_concurrency._run_two_actions_with_lock_handoff`:
    A adquiere el `SELECT ... FOR UPDATE` real, B queda genuinamente
    bloqueado por PostgreSQL hasta que A comitea."""
    lock_acquired = threading.Event()
    b_started = threading.Event()
    allow_a_to_finish = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def worker_a():
        session = session_factory()
        try:
            results["a"] = action_a(session, lock_acquired, allow_a_to_finish)
            session.commit()
        except BaseException as exc:  # noqa: BLE001
            errors["a"] = exc
            session.rollback()
        finally:
            session.close()

    def worker_b():
        session = session_factory()
        try:
            lock_acquired.wait(timeout=5)
            b_started.set()
            results["b"] = action_b(session)
            session.commit()
        except BaseException as exc:  # noqa: BLE001
            errors["b"] = exc
            session.rollback()
        finally:
            session.close()

    thread_a = threading.Thread(target=worker_a)
    thread_b = threading.Thread(target=worker_b)
    thread_a.start()
    thread_b.start()
    assert b_started.wait(timeout=5), "el hilo B nunca comenzo su operacion"
    allow_a_to_finish.set()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)
    return results, errors


def test_two_concurrent_mark_open_requested_one_wins_one_gets_version_conflict(session_factory) -> None:
    setup = _commit_tenant_user_client(session_factory)
    draft_id = _commit_draft(session_factory, setup["tenant_id"], setup["admin_id"], setup["client_id"])

    def _mark_a(session, lock_acquired, allow_a_to_finish):
        admin = session.get(User, setup["admin_id"])
        draft, wa_url = svc.mark_open_requested(
            session, tenant_id=setup["tenant_id"], actor=admin, draft_id=draft_id, expected_version=1
        )
        lock_acquired.set()
        allow_a_to_finish.wait(timeout=5)
        return draft.id, wa_url

    def _mark_b(session):
        admin = session.get(User, setup["admin_id"])
        draft, wa_url = svc.mark_open_requested(
            session, tenant_id=setup["tenant_id"], actor=admin, draft_id=draft_id, expected_version=1
        )
        return draft.id, wa_url

    try:
        results, errors = _run_two_actions_with_lock_handoff(session_factory, _mark_a, _mark_b)
        succeeded = [k for k in ("a", "b") if k in results]
        failed = [k for k in ("a", "b") if k in errors]
        assert len(succeeded) == 1, f"se esperaba exactamente 1 exito: {results} / {errors}"
        assert len(failed) == 1
        assert isinstance(errors[failed[0]], svc.VersionConflictError)

        verify = session_factory()
        try:
            draft = verify.get(CommunicationDraft, draft_id)
            assert draft.status == "OPEN_REQUESTED"
            assert draft.open_requested_at is not None
            assert draft.version == 2, "version debe incrementarse exactamente una vez, nunca dos"
            logs = (
                verify.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == draft_id,
                        AuditLog.action == "COMMUNICATION_OPEN_REQUEST",
                    )
                )
                .scalars()
                .all()
            )
            assert len(logs) == 1, (
                f"se esperaba exactamente 1 entrada de auditoria de la transicion real, "
                f"nunca duplicada: {len(logs)}"
            )
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, setup["tenant_id"])


def test_two_concurrent_mark_sent_after_open_requested_one_wins(session_factory) -> None:
    setup = _commit_tenant_user_client(session_factory)
    draft_id = _commit_draft(session_factory, setup["tenant_id"], setup["admin_id"], setup["client_id"])

    session = session_factory()
    try:
        admin = session.get(User, setup["admin_id"])
        svc.mark_open_requested(
            session, tenant_id=setup["tenant_id"], actor=admin, draft_id=draft_id, expected_version=1
        )
        session.commit()
    finally:
        session.close()

    def _sent_a(session, lock_acquired, allow_a_to_finish):
        admin = session.get(User, setup["admin_id"])
        draft = svc.mark_sent(
            session, tenant_id=setup["tenant_id"], actor=admin, draft_id=draft_id, expected_version=2
        )
        lock_acquired.set()
        allow_a_to_finish.wait(timeout=5)
        return draft.id

    def _sent_b(session):
        admin = session.get(User, setup["admin_id"])
        draft = svc.mark_sent(
            session, tenant_id=setup["tenant_id"], actor=admin, draft_id=draft_id, expected_version=2
        )
        return draft.id

    try:
        results, errors = _run_two_actions_with_lock_handoff(session_factory, _sent_a, _sent_b)
        assert len(results) == 1, f"se esperaba exactamente 1 exito: {results} / {errors}"
        assert len(errors) == 1
        # El perdedor bloquea DESPUES de que el ganador ya comiteo MARKED_SENT:
        # su propia relectura bajo el lock ya no encuentra OPEN_REQUESTED, por
        # lo que la validacion de transicion (que corre antes que la de
        # version) es la que dispara -- ambos errores son la senal correcta
        # de que la segunda operacion nunca se aplico dos veces.
        loser_error = next(iter(errors.values()))
        assert isinstance(
            loser_error, svc.VersionConflictError | svc.InvalidCommunicationTransitionError
        )

        verify = session_factory()
        try:
            draft = verify.get(CommunicationDraft, draft_id)
            assert draft.status == "MARKED_SENT"
            assert draft.version == 3, "version debe incrementarse exactamente una vez, nunca dos"
            assert draft.marked_sent_by == setup["admin_id"]
            assert draft.marked_sent_at is not None
            logs = (
                verify.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == draft_id,
                        AuditLog.action == "COMMUNICATION_MARK_SENT",
                    )
                )
                .scalars()
                .all()
            )
            assert len(logs) == 1, (
                f"se esperaba exactamente 1 entrada de auditoria de la transicion real, "
                f"nunca duplicada: {len(logs)}"
            )
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, setup["tenant_id"])


def test_mark_sent_vs_discard_concurrent_one_wins(session_factory) -> None:
    setup = _commit_tenant_user_client(session_factory)
    draft_id = _commit_draft(session_factory, setup["tenant_id"], setup["admin_id"], setup["client_id"])

    session = session_factory()
    try:
        admin = session.get(User, setup["admin_id"])
        svc.mark_open_requested(
            session, tenant_id=setup["tenant_id"], actor=admin, draft_id=draft_id, expected_version=1
        )
        session.commit()
    finally:
        session.close()

    def _sent(session, lock_acquired, allow_a_to_finish):
        admin = session.get(User, setup["admin_id"])
        draft = svc.mark_sent(
            session, tenant_id=setup["tenant_id"], actor=admin, draft_id=draft_id, expected_version=2
        )
        lock_acquired.set()
        allow_a_to_finish.wait(timeout=5)
        return draft.id

    def _discard(session):
        admin = session.get(User, setup["admin_id"])
        draft = svc.discard_draft(
            session, tenant_id=setup["tenant_id"], actor=admin, draft_id=draft_id, expected_version=2
        )
        return draft.id

    try:
        results, errors = _run_two_actions_with_lock_handoff(session_factory, _sent, _discard)
        assert len(results) == 1, f"se esperaba exactamente 1 exito: {results} / {errors}"
        assert len(errors) == 1
        # `_sent` siempre adquiere el lock primero (Seccion del helper de
        # traspaso) y comitea MARKED_SENT; `discard_draft` relee bajo su
        # propio lock y ya no encuentra un estado descartable -- rechazado
        # por transicion invalida (MARKED_SENT no admite DISCARDED), nunca
        # por version, porque la version aun coincidia en ese momento.
        loser_error = next(iter(errors.values()))
        assert isinstance(
            loser_error, svc.VersionConflictError | svc.InvalidCommunicationTransitionError
        )

        verify = session_factory()
        try:
            draft = verify.get(CommunicationDraft, draft_id)
            assert draft.status == "MARKED_SENT"
            assert draft.version == 3
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, setup["tenant_id"])
