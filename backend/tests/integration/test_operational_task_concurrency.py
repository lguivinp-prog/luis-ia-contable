"""Concurrencia real de tareas operativas (Fase 2, Bloque 1, Dominio E,
Seccion 16 del plan): dos conexiones reales independientes contra el mismo
`test_engine`, con `threading.Event`/`Thread.join(timeout=...)` para forzar
sincronizacion real -- nunca un `sleep` como sustituto. Sigue el mismo
patron que `test_tax_payment_concurrency.py`."""

import threading
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.models.audit_log import AuditLog
from app.models.operational_task import (
    OperationalTask,
    TaskChangeHistory,
    TaskComment,
    TaskGenerationRun,
)
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories import operational_task_repository
from app.schemas.task import TaskDueInput
from app.services import operational_task_service
from app.services.operational_task_service import (
    TaskNotOwnedByAsistenteError,
    TaskVersionConflictError,
)

_DUE = date(2026, 2, 12)


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, future=True)


def _commit_tenant_with_users(session_factory) -> dict:
    session = session_factory()
    try:
        tenant = Tenant(name="Tenant concurrencia tareas")
        session.add(tenant)
        session.flush()
        admin = User(
            tenant_id=tenant.id,
            email=f"admin-conc-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Admin concurrencia",
            password_hash=hash_password("TestPassword123!"),
            role="admin",
            is_active=True,
        )
        asistente = User(
            tenant_id=tenant.id,
            email=f"asist-conc-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Asistente concurrencia",
            password_hash=hash_password("TestPassword123!"),
            role="asistente",
            is_active=True,
        )
        session.add_all([admin, asistente])
        session.flush()
        session.commit()
        return {"tenant_id": tenant.id, "admin_id": admin.id, "asistente_id": asistente.id}
    finally:
        session.close()


def _commit_task(session_factory, tenant_id, admin_id, *, assigned_to=None) -> uuid.UUID:
    session = session_factory()
    try:
        admin = session.get(User, admin_id)
        task = operational_task_service.create_task(
            session,
            tenant_id,
            admin,
            title="Tarea de concurrencia",
            description=None,
            task_type="GENERAL",
            priority="NORMAL",
            due=TaskDueInput(due_date=_DUE),
            assigned_to=assigned_to,
            client_id=None,
        )
        session.commit()
        return task.id
    finally:
        session.close()


def _cleanup(session_factory, tenant_id: uuid.UUID) -> None:
    session = session_factory()
    try:
        session.execute(delete(TaskComment).where(TaskComment.tenant_id == tenant_id))
        session.execute(delete(TaskChangeHistory).where(TaskChangeHistory.tenant_id == tenant_id))
        session.execute(delete(OperationalTask).where(OperationalTask.tenant_id == tenant_id))
        session.execute(delete(TaskGenerationRun).where(TaskGenerationRun.tenant_id == tenant_id))
        session.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        session.execute(delete(User).where(User.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()
    finally:
        session.close()


def _run_two_actions_with_lock_handoff(session_factory, action_a, action_b):
    """Ejecuta dos acciones concurrentes que bloquean la MISMA fila
    (`lock_exact_task`), sin ningun sleep de duracion fija:

    1. hilo A ejecuta `action_a`, que adquiere el lock real de Postgres y
       fija `lock_acquired`, luego espera `allow_a_to_finish` antes de
       retornar (y por lo tanto antes de que el `worker` haga `commit()`).
    2. hilo B espera `lock_acquired` y fija `b_started` -- confirmando que ya
       esta a punto de invocar su propia operacion bloqueante -- antes de
       llamar a `action_b`.
    3. la prueba principal (este hilo) espera `b_started` y solo entonces
       fija `allow_a_to_finish`, liberando a A.
    4. A comitea; el `SELECT ... FOR UPDATE` de B, que ya estaba
       genuinamente bloqueado a nivel de PostgreSQL, se desbloquea y B
       continua.
    """
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
        except BaseException as exc:  # noqa: BLE001 -- reportado en el hilo principal
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
        except BaseException as exc:  # noqa: BLE001 -- reportado en el hilo principal
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


def test_two_concurrent_completes_one_wins_one_gets_version_conflict(session_factory) -> None:
    setup = _commit_tenant_with_users(session_factory)
    task_id = _commit_task(session_factory, setup["tenant_id"], setup["admin_id"])

    def _complete_a(session, lock_acquired, allow_a_to_finish):
        admin = session.get(User, setup["admin_id"])
        task = operational_task_service.complete_task(
            session, setup["tenant_id"], admin, task_id, expected_version=1
        )
        lock_acquired.set()
        allow_a_to_finish.wait(timeout=5)
        return task.id

    def _complete_b(session):
        admin = session.get(User, setup["admin_id"])
        task = operational_task_service.complete_task(
            session, setup["tenant_id"], admin, task_id, expected_version=1
        )
        return task.id

    try:
        results, errors = _run_two_actions_with_lock_handoff(session_factory, _complete_a, _complete_b)
        succeeded = [k for k in ("a", "b") if k in results]
        failed = [k for k in ("a", "b") if k in errors]
        assert len(succeeded) == 1, f"se esperaba exactamente 1 exito: {results} / {errors}"
        assert len(failed) == 1
        assert isinstance(errors[failed[0]], TaskVersionConflictError)
    finally:
        _cleanup(session_factory, setup["tenant_id"])


def test_reassign_vs_complete_concurrent_one_wins(session_factory) -> None:
    setup = _commit_tenant_with_users(session_factory)
    task_id = _commit_task(session_factory, setup["tenant_id"], setup["admin_id"])

    def _reassign(session, lock_acquired, allow_a_to_finish):
        admin = session.get(User, setup["admin_id"])
        task = operational_task_service.reassign_task(
            session,
            setup["tenant_id"],
            admin,
            task_id,
            expected_version=1,
            assigned_to=setup["admin_id"],
        )
        lock_acquired.set()
        allow_a_to_finish.wait(timeout=5)
        return task.id

    def _complete(session):
        admin = session.get(User, setup["admin_id"])
        task = operational_task_service.complete_task(
            session, setup["tenant_id"], admin, task_id, expected_version=1
        )
        return task.id

    try:
        results, errors = _run_two_actions_with_lock_handoff(session_factory, _reassign, _complete)
        assert len(results) == 1
        assert len(errors) == 1
        assert isinstance(next(iter(errors.values())), TaskVersionConflictError)
    finally:
        _cleanup(session_factory, setup["tenant_id"])


def test_complete_vs_cancel_concurrent_one_wins(session_factory) -> None:
    setup = _commit_tenant_with_users(session_factory)
    task_id = _commit_task(session_factory, setup["tenant_id"], setup["admin_id"])

    def _complete(session, lock_acquired, allow_a_to_finish):
        admin = session.get(User, setup["admin_id"])
        task = operational_task_service.complete_task(
            session, setup["tenant_id"], admin, task_id, expected_version=1
        )
        lock_acquired.set()
        allow_a_to_finish.wait(timeout=5)
        return task.id

    def _cancel(session):
        admin = session.get(User, setup["admin_id"])
        task = operational_task_service.cancel_task(
            session,
            setup["tenant_id"],
            admin,
            task_id,
            expected_version=1,
            reason="Cancelacion de prueba de concurrencia",
        )
        return task.id

    try:
        results, errors = _run_two_actions_with_lock_handoff(session_factory, _complete, _cancel)
        assert len(results) == 1, f"se esperaba exactamente 1 exito: {results} / {errors}"
        assert len(errors) == 1
        assert isinstance(next(iter(errors.values())), TaskVersionConflictError)

        verify = session_factory()
        try:
            task = verify.get(OperationalTask, task_id)
            assert task.status in ("COMPLETED", "CANCELLED")
            assert task.version == 2
            history = (
                verify.execute(
                    select(TaskChangeHistory).where(TaskChangeHistory.task_id == task_id)
                )
                .scalars()
                .all()
            )
            # CREATED + exactamente 1 evento terminal -- nunca ambos aplicados.
            assert len(history) == 2
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, setup["tenant_id"])


def test_two_concurrent_reschedules_same_expected_version(session_factory) -> None:
    setup = _commit_tenant_with_users(session_factory)
    task_id = _commit_task(session_factory, setup["tenant_id"], setup["admin_id"])

    def _reschedule_to(new_date):
        def _action(session, lock_acquired, allow_a_to_finish):
            admin = session.get(User, setup["admin_id"])
            task = operational_task_service.reschedule_task(
                session,
                setup["tenant_id"],
                admin,
                task_id,
                expected_version=1,
                due=TaskDueInput(due_date=new_date),
            )
            lock_acquired.set()
            allow_a_to_finish.wait(timeout=5)
            return task.id

        return _action

    def _reschedule_to_waiting(new_date):
        def _action(session):
            admin = session.get(User, setup["admin_id"])
            task = operational_task_service.reschedule_task(
                session,
                setup["tenant_id"],
                admin,
                task_id,
                expected_version=1,
                due=TaskDueInput(due_date=new_date),
            )
            return task.id

        return _action

    try:
        results, errors = _run_two_actions_with_lock_handoff(
            session_factory,
            _reschedule_to(date(2026, 3, 1)),
            _reschedule_to_waiting(date(2026, 3, 2)),
        )
        assert len(results) == 1, f"se esperaba exactamente 1 exito: {results} / {errors}"
        assert len(errors) == 1
        assert isinstance(next(iter(errors.values())), TaskVersionConflictError)

        verify = session_factory()
        try:
            task = verify.get(OperationalTask, task_id)
            assert task.version == 2
            assert task.due_at.date() in (date(2026, 3, 1), date(2026, 3, 2))
            history = (
                verify.execute(
                    select(TaskChangeHistory).where(
                        TaskChangeHistory.task_id == task_id,
                        TaskChangeHistory.event_type == "RESCHEDULED",
                    )
                )
                .scalars()
                .all()
            )
            assert len(history) == 1
        finally:
            verify.close()
    finally:
        _cleanup(session_factory, setup["tenant_id"])


def test_reassign_vs_reschedule_concurrent_one_wins(session_factory) -> None:
    setup = _commit_tenant_with_users(session_factory)
    task_id = _commit_task(session_factory, setup["tenant_id"], setup["admin_id"])

    def _reassign(session, lock_acquired, allow_a_to_finish):
        admin = session.get(User, setup["admin_id"])
        task = operational_task_service.reassign_task(
            session,
            setup["tenant_id"],
            admin,
            task_id,
            expected_version=1,
            assigned_to=setup["admin_id"],
        )
        lock_acquired.set()
        allow_a_to_finish.wait(timeout=5)
        return task.id

    def _reschedule(session):
        admin = session.get(User, setup["admin_id"])
        task = operational_task_service.reschedule_task(
            session,
            setup["tenant_id"],
            admin,
            task_id,
            expected_version=1,
            due=TaskDueInput(due_date=date(2026, 3, 5)),
        )
        return task.id

    try:
        results, errors = _run_two_actions_with_lock_handoff(session_factory, _reassign, _reschedule)
        assert len(results) == 1, f"se esperaba exactamente 1 exito: {results} / {errors}"
        assert len(errors) == 1
        assert isinstance(next(iter(errors.values())), TaskVersionConflictError)
    finally:
        _cleanup(session_factory, setup["tenant_id"])


def _install_lock_handoff(monkeypatch, target_task_id: uuid.UUID):
    """Envuelve `operational_task_repository.lock_exact_task` (compartida por
    `operational_task_service` y `task_generation_service`) para que la
    PRIMERA llamada real que bloquee `target_task_id` retenga su transaccion
    abierta -- sin ningun sleep de duracion fija -- hasta que la prueba
    confirme, via `second_attempt_started`, que la SEGUNDA transaccion ya
    invoco su propia operacion bloqueante (que por construccion queda
    genuinamente bloqueada por PostgreSQL mientras la primera no libere).
    Devuelve `(first_acquired, second_attempt_started)`: la prueba espera
    `first_acquired` antes de lanzar la segunda operacion, fija
    `second_attempt_started` justo antes de invocarla, y la primera
    transaccion recien entonces continua (y su `worker` hace `commit()`)."""
    original = operational_task_repository.lock_exact_task
    state = {"first_call_done": False}
    guard = threading.Lock()
    first_acquired = threading.Event()
    second_attempt_started = threading.Event()

    def wrapped(db, tenant_id, task_id):
        row = original(db, tenant_id, task_id)
        if row is not None and row.id == target_task_id:
            with guard:
                is_first = not state["first_call_done"]
                state["first_call_done"] = True
            if is_first:
                first_acquired.set()
                assert second_attempt_started.wait(timeout=5), (
                    "la segunda transaccion nunca comenzo su intento de bloqueo"
                )
        return row

    monkeypatch.setattr(operational_task_repository, "lock_exact_task", wrapped)
    return first_acquired, second_attempt_started


def _commit_declaration_with_generated_task(session_factory, *, due_date: date) -> dict:
    """Crea tenant/admin/obligacion/cliente/cronograma/periodo/declaracion
    reales y comprometidos, y genera (via el servicio real de sincronizacion)
    su tarea `DECLARATION_FILING`. Devuelve solo IDs -- nunca objetos ORM,
    para evitar instancias desvinculadas al cruzar hilos/conexiones."""
    from app.core import clock as clock_module
    from app.models.client import Client
    from app.models.obligation import ObligationCatalog
    from app.services import declaration_service, task_generation_service
    from app.services.ruc_validator import compute_check_digit
    from tests.integration._declaration_helpers import (
        make_client_obligation,
        make_entry,
        make_monthly_period,
        make_source,
        make_version,
    )

    session = session_factory()
    try:
        tenant = Tenant(name="Tenant resolucion concurrencia")
        session.add(tenant)
        session.flush()
        admin = User(
            tenant_id=tenant.id,
            email=f"admin-res-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Admin resolucion",
            password_hash=hash_password("TestPassword123!"),
            role="admin",
            is_active=True,
        )
        session.add(admin)
        session.flush()
        obligation = ObligationCatalog(
            code=f"RES-{uuid.uuid4().hex[:6]}",
            name="Obligacion resolucion concurrencia",
            periodicity="mensual",
            source="fixture",
            source_version="v1",
            is_active=True,
        )
        session.add(obligation)
        session.flush()

        base8 = f"{uuid.uuid4().int % 10**8:08d}"
        first_ten = "20" + base8
        ruc = first_ten + str(compute_check_digit(first_ten))
        client = Client(
            tenant_id=tenant.id,
            ruc=ruc,
            business_name="Cliente resolucion concurrencia",
            person_type="juridica",
            ruc_status="ACTIVO",
            fiscal_address_condition="HABIDO",
            tax_regime="GENERAL",
            service_status="activo",
        )
        session.add(client)
        session.flush()
        co = make_client_obligation(session, tenant.id, client.id, obligation.id)
        source = make_source(session, tenant.id, obligation.id)
        version = make_version(session, tenant.id, source.id, fiscal_year=due_date.year)
        make_entry(
            session,
            tenant.id,
            version.id,
            tax_period=f"{due_date.year}-{due_date.month:02d}",
            digits=list(range(10)),
        )
        period = make_monthly_period(
            session, tenant.id, fiscal_year=due_date.year, month=due_date.month
        )
        declaration = declaration_service.create_declaration(
            session,
            tenant.id,
            admin.id,
            client_id=client.id,
            client_obligation_id=co.id,
            tax_period_id=period.id,
            origin="MANUAL",
            sync_documents=False,
        )
        declaration.current_effective_due_date = due_date
        session.flush()
        session.commit()
        tenant_id = tenant.id
        admin_id = admin.id
        declaration_id = declaration.id
        obligation_id = obligation.id
    finally:
        session.close()

    clock_module.set_clock(lambda: datetime.combine(due_date, datetime.min.time(), tzinfo=UTC))
    try:
        session = session_factory()
        try:
            run = task_generation_service.sync_operational_tasks(
                session, tenant_id, trigger_source="MANUAL", triggered_by=admin_id
            )
            session.commit()
            assert run.summary["created"] == 1, run.summary
        finally:
            session.close()

        verify = session_factory()
        try:
            task = (
                verify.execute(
                    select(OperationalTask).where(
                        OperationalTask.tenant_id == tenant_id,
                        OperationalTask.generation_key == f"DECL_FILING:{declaration_id}",
                    )
                )
                .scalars()
                .one()
            )
            task_id = task.id
            task_version = task.version
        finally:
            verify.close()
    finally:
        clock_module.reset_clock()

    return {
        "tenant_id": tenant_id,
        "admin_id": admin_id,
        "declaration_id": declaration_id,
        "obligation_id": obligation_id,
        "task_id": task_id,
        "task_version": task_version,
    }


def _cleanup_declaration_task_data(session_factory, tenant_id: uuid.UUID, obligation_id: uuid.UUID) -> None:
    _cleanup_sync_test_data(session_factory, tenant_id, obligation_id)


def test_sync_realigns_due_date_vs_human_reschedule_concurrent(session_factory, monkeypatch) -> None:
    from app.core import clock as clock_module
    from app.services import task_generation_service

    setup = _commit_declaration_with_generated_task(session_factory, due_date=date(2026, 2, 12))

    # Cambiamos el vencimiento real de la declaracion DESPUES de generar la
    # tarea -- la sincronizacion debe realinear source_due_at/due_at.
    session = session_factory()
    try:
        from app.models.declaration import Declaration

        declaration = session.get(Declaration, setup["declaration_id"])
        declaration.current_effective_due_date = date(2026, 2, 20)
        session.commit()
    finally:
        session.close()

    first_acquired, second_attempt_started = _install_lock_handoff(monkeypatch, setup["task_id"])
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def worker_sync():
        session = session_factory()
        try:
            clock_module.set_clock(lambda: datetime(2026, 2, 15, 12, 0, tzinfo=UTC))
            task_generation_service.sync_operational_tasks(
                session, setup["tenant_id"], trigger_source="MANUAL", triggered_by=setup["admin_id"]
            )
            session.commit()
            results["sync"] = "ok"
        except BaseException as exc:  # noqa: BLE001
            errors["sync"] = exc
            session.rollback()
        finally:
            clock_module.reset_clock()
            session.close()

    def worker_human():
        first_acquired.wait(timeout=5)
        session = session_factory()
        try:
            admin = session.get(User, setup["admin_id"])
            second_attempt_started.set()
            operational_task_service.reschedule_task(
                session,
                setup["tenant_id"],
                admin,
                setup["task_id"],
                expected_version=setup["task_version"],
                due=TaskDueInput(due_date=date(2026, 3, 10)),
            )
            session.commit()
            results["human"] = "ok"
        except BaseException as exc:  # noqa: BLE001
            errors["human"] = exc
            session.rollback()
        finally:
            session.close()

    thread_sync = threading.Thread(target=worker_sync)
    thread_sync.start()
    thread_human = threading.Thread(target=worker_human)
    thread_human.start()
    thread_sync.join(timeout=15)
    thread_human.join(timeout=15)

    try:
        assert "sync" in results, f"la sincronizacion no debia fallar: {errors}"
        assert "human" in errors, f"se esperaba 409 para el humano: {results}"
        assert isinstance(errors["human"], TaskVersionConflictError)

        verify = session_factory()
        try:
            from app.core.timezone import end_of_day_utc

            task = verify.get(OperationalTask, setup["task_id"])
            assert task.due_is_overridden is False
            assert task.due_at == end_of_day_utc(date(2026, 2, 20))
            assert task.version == setup["task_version"] + 1
            history = (
                verify.execute(
                    select(TaskChangeHistory).where(
                        TaskChangeHistory.task_id == setup["task_id"],
                        TaskChangeHistory.event_type == "SOURCE_DUE_UPDATED",
                    )
                )
                .scalars()
                .all()
            )
            assert len(history) == 1
        finally:
            verify.close()
    finally:
        _cleanup_declaration_task_data(session_factory, setup["tenant_id"], setup["obligation_id"])


def test_sync_resolves_completed_vs_human_complete_concurrent(session_factory, monkeypatch) -> None:
    from app.core import clock as clock_module
    from app.models.declaration import Declaration
    from app.services import task_generation_service

    setup = _commit_declaration_with_generated_task(session_factory, due_date=date(2026, 2, 12))

    session = session_factory()
    try:
        declaration = session.get(Declaration, setup["declaration_id"])
        declaration.filing_status = "FILED"
        session.commit()
    finally:
        session.close()

    first_acquired, second_attempt_started = _install_lock_handoff(monkeypatch, setup["task_id"])
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def worker_sync():
        session = session_factory()
        try:
            clock_module.set_clock(lambda: datetime(2026, 2, 10, 12, 0, tzinfo=UTC))
            task_generation_service.sync_operational_tasks(
                session, setup["tenant_id"], trigger_source="MANUAL", triggered_by=setup["admin_id"]
            )
            session.commit()
            results["sync"] = "ok"
        except BaseException as exc:  # noqa: BLE001
            errors["sync"] = exc
            session.rollback()
        finally:
            clock_module.reset_clock()
            session.close()

    def worker_human():
        first_acquired.wait(timeout=5)
        session = session_factory()
        try:
            admin = session.get(User, setup["admin_id"])
            second_attempt_started.set()
            operational_task_service.complete_task(
                session,
                setup["tenant_id"],
                admin,
                setup["task_id"],
                expected_version=setup["task_version"],
            )
            session.commit()
            results["human"] = "ok"
        except BaseException as exc:  # noqa: BLE001
            errors["human"] = exc
            session.rollback()
        finally:
            session.close()

    thread_sync = threading.Thread(target=worker_sync)
    thread_sync.start()
    thread_human = threading.Thread(target=worker_human)
    thread_human.start()
    thread_sync.join(timeout=15)
    thread_human.join(timeout=15)

    try:
        assert "sync" in results, f"la sincronizacion no debia fallar: {errors}"
        assert "human" in errors, f"se esperaba 409 para el humano: {results}"
        assert isinstance(errors["human"], TaskVersionConflictError)

        verify = session_factory()
        try:
            task = verify.get(OperationalTask, setup["task_id"])
            assert task.status == "COMPLETED"
            assert task.resolution_origin == "SYSTEM"
            assert task.completed_by is None
            assert task.version == setup["task_version"] + 1
            history = (
                verify.execute(
                    select(TaskChangeHistory).where(
                        TaskChangeHistory.task_id == setup["task_id"],
                        TaskChangeHistory.event_type == "SOURCE_COMPLETED",
                    )
                )
                .scalars()
                .all()
            )
            assert len(history) == 1
        finally:
            verify.close()
    finally:
        _cleanup_declaration_task_data(session_factory, setup["tenant_id"], setup["obligation_id"])


def test_sync_resolves_cancelled_vs_human_reschedule_concurrent(session_factory, monkeypatch) -> None:
    from app.core import clock as clock_module
    from app.models.declaration import Declaration
    from app.services import task_generation_service

    setup = _commit_declaration_with_generated_task(session_factory, due_date=date(2026, 2, 12))

    session = session_factory()
    try:
        declaration = session.get(Declaration, setup["declaration_id"])
        declaration.workflow_status = "CANCELLED"
        session.commit()
    finally:
        session.close()

    first_acquired, second_attempt_started = _install_lock_handoff(monkeypatch, setup["task_id"])
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def worker_sync():
        session = session_factory()
        try:
            clock_module.set_clock(lambda: datetime(2026, 2, 10, 12, 0, tzinfo=UTC))
            task_generation_service.sync_operational_tasks(
                session, setup["tenant_id"], trigger_source="MANUAL", triggered_by=setup["admin_id"]
            )
            session.commit()
            results["sync"] = "ok"
        except BaseException as exc:  # noqa: BLE001
            errors["sync"] = exc
            session.rollback()
        finally:
            clock_module.reset_clock()
            session.close()

    def worker_human():
        first_acquired.wait(timeout=5)
        session = session_factory()
        try:
            admin = session.get(User, setup["admin_id"])
            second_attempt_started.set()
            operational_task_service.reschedule_task(
                session,
                setup["tenant_id"],
                admin,
                setup["task_id"],
                expected_version=setup["task_version"],
                due=TaskDueInput(due_date=date(2026, 3, 15)),
            )
            session.commit()
            results["human"] = "ok"
        except BaseException as exc:  # noqa: BLE001
            errors["human"] = exc
            session.rollback()
        finally:
            session.close()

    thread_sync = threading.Thread(target=worker_sync)
    thread_sync.start()
    thread_human = threading.Thread(target=worker_human)
    thread_human.start()
    thread_sync.join(timeout=15)
    thread_human.join(timeout=15)

    try:
        assert "sync" in results, f"la sincronizacion no debia fallar: {errors}"
        assert "human" in errors, f"se esperaba 409 para el humano: {results}"
        assert isinstance(errors["human"], TaskVersionConflictError)

        verify = session_factory()
        try:
            task = verify.get(OperationalTask, setup["task_id"])
            assert task.status == "CANCELLED"
            assert task.resolution_origin == "SYSTEM"
            assert task.cancelled_by is None
            assert task.cancellation_reason == "La declaración fue cancelada"
            assert task.version == setup["task_version"] + 1
            history = (
                verify.execute(
                    select(TaskChangeHistory).where(
                        TaskChangeHistory.task_id == setup["task_id"],
                        TaskChangeHistory.event_type == "SOURCE_CANCELLED",
                    )
                )
                .scalars()
                .all()
            )
            assert len(history) == 1
        finally:
            verify.close()
    finally:
        _cleanup_declaration_task_data(session_factory, setup["tenant_id"], setup["obligation_id"])


def test_two_syncs_update_existing_tasks_ordered_by_id_without_deadlock(session_factory) -> None:
    """Dos tareas generadas existentes (A y B), bajo el MISMO tenant, que
    ambas ejecuciones deben realinear. La consulta que descubre candidatos
    siempre ordena por `operational_tasks.id ASC`
    (`list_non_terminal_generated_tasks`) -- determinista por construccion,
    nunca dependiente del orden de llegada de cada hilo -- por eso dos
    sincronizaciones concurrentes que deben mutar las MISMAS filas nunca se
    interbloquean, sin importar en que orden cada hilo las descubra."""
    from app.core import clock as clock_module
    from app.models.client import Client
    from app.models.declaration import Declaration
    from app.services import declaration_service, task_generation_service
    from app.services.ruc_validator import compute_check_digit
    from tests.integration._declaration_helpers import (
        make_client_obligation,
        make_entry,
        make_monthly_period,
    )

    setup = _commit_declaration_with_generated_task(session_factory, due_date=date(2026, 2, 12))
    tenant_id = setup["tenant_id"]

    # Segunda declaracion/tarea bajo el MISMO tenant y obligacion, reutilizando
    # la misma fuente/version de cronograma ya activa (ver
    # `_operational_task_helpers.make_second_month_declaration`: una fuente
    # nueva por declaracion rompe la resolucion unica por obligacion).
    session = session_factory()
    try:
        base8 = f"{uuid.uuid4().int % 10**8:08d}"
        first_ten = "20" + base8
        ruc = first_ten + str(compute_check_digit(first_ten))
        client_b = Client(
            tenant_id=tenant_id,
            ruc=ruc,
            business_name="Cliente B resolucion concurrencia",
            person_type="juridica",
            ruc_status="ACTIVO",
            fiscal_address_condition="HABIDO",
            tax_regime="GENERAL",
            service_status="activo",
        )
        session.add(client_b)
        session.flush()
        co_b = make_client_obligation(session, tenant_id, client_b.id, setup["obligation_id"])
        shared_version_id = session.get(Declaration, setup["declaration_id"]).schedule_version_id
        make_entry(
            session, tenant_id, shared_version_id, tax_period="2026-03", digits=list(range(10))
        )
        period_b = make_monthly_period(session, tenant_id, fiscal_year=2026, month=3)
        declaration_b = declaration_service.create_declaration(
            session,
            tenant_id,
            setup["admin_id"],
            client_id=client_b.id,
            client_obligation_id=co_b.id,
            tax_period_id=period_b.id,
            origin="MANUAL",
            sync_documents=False,
        )
        declaration_b.current_effective_due_date = date(2026, 2, 12)
        session.flush()
        session.commit()
        declaration_b_id = declaration_b.id
    finally:
        session.close()

    clock_module.set_clock(lambda: datetime(2026, 2, 10, 12, 0, tzinfo=UTC))
    try:
        session = session_factory()
        try:
            run = task_generation_service.sync_operational_tasks(
                session, tenant_id, trigger_source="MANUAL", triggered_by=setup["admin_id"]
            )
            session.commit()
            assert run.summary["created"] == 1, run.summary
        finally:
            session.close()
    finally:
        clock_module.reset_clock()

    # Ahora ambas declaraciones (A y B) cambian de vencimiento -- las dos
    # tareas existentes requieren realineacion en la proxima sincronizacion.
    session = session_factory()
    try:
        decl_a = session.get(Declaration, setup["declaration_id"])
        decl_b = session.get(Declaration, declaration_b_id)
        decl_a.current_effective_due_date = date(2026, 2, 25)
        decl_b.current_effective_due_date = date(2026, 2, 26)
        session.commit()
    finally:
        session.close()

    errors: list[BaseException] = []

    def worker():
        session = session_factory()
        try:
            clock_module.set_clock(lambda: datetime(2026, 2, 15, 12, 0, tzinfo=UTC))
            task_generation_service.sync_operational_tasks(
                session, tenant_id, trigger_source="MANUAL", triggered_by=setup["admin_id"]
            )
            session.commit()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            session.rollback()
        finally:
            clock_module.reset_clock()
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    try:
        assert not errors, f"no se esperaban errores/deadlocks: {errors}"

        verify = session_factory()
        try:
            tasks = (
                verify.execute(select(OperationalTask).where(OperationalTask.tenant_id == tenant_id))
                .scalars()
                .all()
            )
            from app.core.timezone import end_of_day_utc

            assert len(tasks) == 2
            due_ats = sorted(t.due_at for t in tasks)
            assert due_ats == [end_of_day_utc(date(2026, 2, 25)), end_of_day_utc(date(2026, 2, 26))]

            for task in tasks:
                history = (
                    verify.execute(
                        select(TaskChangeHistory).where(
                            TaskChangeHistory.task_id == task.id,
                            TaskChangeHistory.event_type == "SOURCE_DUE_UPDATED",
                        )
                    )
                    .scalars()
                    .all()
                )
                assert len(history) == 1, f"realineacion duplicada en tarea {task.id}: {len(history)}"
        finally:
            verify.close()
    finally:
        _cleanup_declaration_task_data(session_factory, tenant_id, setup["obligation_id"])


@pytest.mark.parametrize("comment_first", [True, False])
def test_comment_vs_reassign_race_both_orders(session_factory, comment_first: bool) -> None:
    setup = _commit_tenant_with_users(session_factory)
    task_id = _commit_task(
        session_factory, setup["tenant_id"], setup["admin_id"], assigned_to=setup["asistente_id"]
    )

    first_locked = threading.Event()
    second_started = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def _do_comment(session):
        asistente = session.get(User, setup["asistente_id"])
        return operational_task_service.add_comment(
            session, setup["tenant_id"], asistente, task_id, body="Comentario de la carrera"
        )

    def _do_reassign(session):
        admin = session.get(User, setup["admin_id"])
        return operational_task_service.reassign_task(
            session, setup["tenant_id"], admin, task_id, expected_version=1, assigned_to=setup["admin_id"]
        )

    def worker(name, action, is_first):
        session = session_factory()
        try:
            if not is_first:
                first_locked.wait(timeout=5)
                second_started.set()
            results[name] = action(session)
            if is_first:
                first_locked.set()
                assert second_started.wait(timeout=5), "el segundo hilo nunca comenzo su operacion"
            session.commit()
        except BaseException as exc:  # noqa: BLE001
            errors[name] = exc
            session.rollback()
            if is_first:
                first_locked.set()
        finally:
            session.close()

    first_action = _do_comment if comment_first else _do_reassign
    second_action = _do_reassign if comment_first else _do_comment
    first_name = "comment" if comment_first else "reassign"
    second_name = "reassign" if comment_first else "comment"

    thread_first = threading.Thread(target=worker, args=(first_name, first_action, True))
    thread_second = threading.Thread(target=worker, args=(second_name, second_action, False))
    thread_first.start()
    thread_second.start()
    thread_first.join(timeout=15)
    thread_second.join(timeout=15)

    try:
        if comment_first:
            # El comentario adquirio el lock primero -> se acepta siempre.
            assert "comment" in results
            # La reasignacion ocurre despues, sobre la fila ya liberada -> tambien procede.
            assert "reassign" in results or isinstance(
                errors.get("reassign"), TaskVersionConflictError
            )
        else:
            # La reasignacion ocurrio primero -> el comentario, al bloquear
            # despues, relee assigned_to ya cambiado a admin y el asistente
            # ya no es el responsable -> 403.
            assert "reassign" in results
            assert isinstance(errors.get("comment"), TaskNotOwnedByAsistenteError) or (
                "comment" in results
            )
    finally:
        _cleanup(session_factory, setup["tenant_id"])


def _install_forced_discovery_and_capture(monkeypatch, task_generation_service, thread_key_order):
    """Fuerza el orden en que `_discover_declaration_filing` entrega sus
    candidatos, distinto por hilo segun `threading.current_thread().name`
    (mapeado en `thread_key_order`), y captura -- por hilo -- tanto el orden
    de descubrimiento realmente entregado como el orden normalizado que
    `_insert_candidates` calcula justo antes de insertar (replicando su
    misma clave de ordenamiento `generation_key ASC`, sin alterar en nada la
    logica real: el wrapper delega siempre en la funcion original)."""
    original_discover = task_generation_service._discover_declaration_filing
    original_insert = task_generation_service._insert_candidates
    discovery_order: dict[str, list[str]] = {}
    normalized_order: dict[str, list[str]] = {}
    lock = threading.Lock()

    def wrapped_discover(db, tenant_id, today):
        candidates = original_discover(db, tenant_id, today)
        by_key = {c["generation_key"]: c for c in candidates}
        thread_name = threading.current_thread().name
        forced_keys = thread_key_order.get(thread_name)
        ordered = [by_key[k] for k in forced_keys if k in by_key] if forced_keys else candidates
        with lock:
            discovery_order[thread_name] = [c["generation_key"] for c in ordered]
        return ordered

    def wrapped_insert(db, tenant_id, run, candidates, now):
        thread_name = threading.current_thread().name
        sorted_keys = [c["generation_key"] for c in sorted(candidates, key=lambda c: c["generation_key"])]
        with lock:
            normalized_order[thread_name] = sorted_keys
        return original_insert(db, tenant_id, run, candidates, now)

    monkeypatch.setattr(task_generation_service, "_discover_declaration_filing", wrapped_discover)
    monkeypatch.setattr(task_generation_service, "_insert_candidates", wrapped_insert)
    return discovery_order, normalized_order


def test_two_syncs_insert_same_generation_keys_in_opposite_discovery_order_without_deadlock(
    session_factory, monkeypatch
) -> None:
    """Dos sincronizaciones concurrentes compiten por LAS MISMAS dos
    `generation_key` X e Y (nunca conjuntos disjuntos): la transaccion A las
    descubre en orden [Y, X] y la transaccion B las descubre en orden
    [X, Y] -- orden opuesto forzado explicitamente via monkeypatch de
    `_discover_declaration_filing`, no dejado al azar del planificador de
    Postgres. `_insert_candidates` normaliza siempre a `generation_key ASC`
    antes de insertar (Seccion 8 del plan): ambas terminan insertando en el
    MISMO orden `[X, Y]`, lo que estructuralmente impide el interbloqueo
    clasico de indice unico (A esperando el lock de Y mientras sostiene el
    de X, B esperando el lock de X mientras sostiene el de Y) y en su lugar
    produce una resolucion limpia: quien alcance primero la insercion de la
    clave menor termina creando ambas, la otra obtiene `already_exists` para
    ambas."""
    from app.models.client import Client
    from app.models.obligation import ObligationCatalog
    from app.services import declaration_service, task_generation_service
    from app.services.ruc_validator import compute_check_digit
    from tests.integration._declaration_helpers import (
        make_client_obligation,
        make_entry,
        make_monthly_period,
        make_source,
        make_version,
    )

    session = session_factory()
    try:
        tenant = Tenant(name="Tenant sync concurrencia")
        session.add(tenant)
        session.flush()
        admin = User(
            tenant_id=tenant.id,
            email=f"admin-sync-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Admin sync",
            password_hash=hash_password("TestPassword123!"),
            role="admin",
            is_active=True,
        )
        session.add(admin)
        session.flush()
        obligation = ObligationCatalog(
            code=f"SYNC-{uuid.uuid4().hex[:6]}",
            name="Obligacion sync concurrencia",
            periodicity="mensual",
            source="fixture",
            source_version="v1",
            is_active=True,
        )
        session.add(obligation)
        session.flush()

        # Una unica fuente/version activa por obligacion (igual que en
        # produccion, ver `get_active_source_for_obligation`) con una
        # entrada de cronograma por mes -- crear una fuente nueva por
        # declaracion rompe esa resolucion unica (ver
        # `_operational_task_helpers.make_second_month_declaration`).
        shared_source = make_source(session, tenant.id, obligation.id, name="Fuente compartida")
        shared_version = make_version(session, tenant.id, shared_source.id, fiscal_year=2026)

        declaration_ids = []
        for month in (1, 2):
            base8 = f"{uuid.uuid4().int % 10**8:08d}"
            first_ten = "20" + base8
            ruc = first_ten + str(compute_check_digit(first_ten))
            client = Client(
                tenant_id=tenant.id,
                ruc=ruc,
                business_name=f"Cliente sync {month}",
                person_type="juridica",
                ruc_status="ACTIVO",
                fiscal_address_condition="HABIDO",
                tax_regime="GENERAL",
                service_status="activo",
            )
            session.add(client)
            session.flush()
            co = make_client_obligation(session, tenant.id, client.id, obligation.id)
            make_entry(
                session,
                tenant.id,
                shared_version.id,
                tax_period=f"2026-{month:02d}",
                digits=list(range(10)),
            )
            period = make_monthly_period(session, tenant.id, fiscal_year=2026, month=month)
            declaration = declaration_service.create_declaration(
                session,
                tenant.id,
                admin.id,
                client_id=client.id,
                client_obligation_id=co.id,
                tax_period_id=period.id,
                origin="MANUAL",
                sync_documents=False,
            )
            declaration_ids.append(declaration.id)
        session.commit()
        tenant_id = tenant.id
        admin_id = admin.id
        obligation_id = obligation.id
    finally:
        session.close()

    key_x, key_y = (f"DECL_FILING:{did}" for did in declaration_ids)
    key_min, key_max = sorted([key_x, key_y])

    discovery_order, normalized_order = _install_forced_discovery_and_capture(
        monkeypatch,
        task_generation_service,
        {
            "sync_thread_a": [key_max, key_min],  # A descubre [Y, X]
            "sync_thread_b": [key_min, key_max],  # B descubre [X, Y]
        },
    )

    from app.core import clock as clock_module

    clock_module.set_clock(lambda: datetime(2026, 2, 10, 12, 0, tzinfo=UTC))
    try:
        results: dict[str, dict] = {}
        errors: dict[str, BaseException] = {}

        def worker(name):
            session = session_factory()
            try:
                run = task_generation_service.sync_operational_tasks(
                    session, tenant_id, trigger_source="MANUAL", triggered_by=admin_id
                )
                session.commit()
                results[name] = run.summary
            except BaseException as exc:  # noqa: BLE001
                errors[name] = exc
                session.rollback()
            finally:
                session.close()

        thread_a = threading.Thread(target=worker, args=("a",), name="sync_thread_a")
        thread_b = threading.Thread(target=worker, args=("b",), name="sync_thread_b")
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=20)
        thread_b.join(timeout=20)

        assert not errors, f"no se esperaban errores/deadlocks/IntegrityError: {errors}"
        assert "a" in results and "b" in results, f"ambas transacciones debian completar: {errors}"

        candidate_keys_thread_a = set(discovery_order["sync_thread_a"])
        candidate_keys_thread_b = set(discovery_order["sync_thread_b"])
        discovery_order_thread_a = discovery_order["sync_thread_a"]
        discovery_order_thread_b = discovery_order["sync_thread_b"]
        normalized_order_thread_a = normalized_order["sync_thread_a"]
        normalized_order_thread_b = normalized_order["sync_thread_b"]

        # Ambas transacciones compiten por las MISMAS dos generation_key.
        assert candidate_keys_thread_a == {key_x, key_y}
        assert candidate_keys_thread_b == {key_x, key_y}
        # Descubiertas en orden opuesto -- forzado explicitamente, no al azar.
        assert discovery_order_thread_a != discovery_order_thread_b
        assert discovery_order_thread_a == [key_max, key_min]
        assert discovery_order_thread_b == [key_min, key_max]
        # Tras normalizar, ambas insertan en el MISMO orden [X, Y] (generation_key ASC).
        assert normalized_order_thread_a == [key_min, key_max]
        assert normalized_order_thread_b == [key_min, key_max]

        summaries = [results["a"], results["b"]]
        created_counts = sorted(s["created"] for s in summaries)
        already_exists_counts = sorted(s["already_exists"] for s in summaries)
        assert created_counts == [0, 2], f"una transaccion debia crear ambas claves: {summaries}"
        assert already_exists_counts == [0, 2], (
            f"la otra transaccion debia obtener already_exists para ambas: {summaries}"
        )

        verify = session_factory()
        try:
            tasks = (
                verify.execute(select(OperationalTask).where(OperationalTask.tenant_id == tenant_id))
                .scalars()
                .all()
            )
            assert len(tasks) == 2, "total final: exactamente dos tareas, sin duplicados"
            assert {t.generation_key for t in tasks} == {key_x, key_y}

            runs = (
                verify.execute(
                    select(TaskGenerationRun).where(TaskGenerationRun.tenant_id == tenant_id)
                )
                .scalars()
                .all()
            )
            assert len(runs) == 2
            assert all(r.completed_at is not None for r in runs), "ambos runs deben terminar con completed_at"
        finally:
            verify.close()
    finally:
        clock_module.reset_clock()
        _cleanup_sync_test_data(session_factory, tenant_id, obligation_id)


def _cleanup_sync_test_data(session_factory, tenant_id: uuid.UUID, obligation_id: uuid.UUID) -> None:
    """Limpieza especifica de esta prueba: ademas de las tablas de tareas,
    commitea una declaracion real completa (historial, cronograma, cliente),
    a diferencia de `_cleanup` (que solo cubre datos de tareas)."""
    from app.models.client import Client
    from app.models.declaration import (
        Declaration,
        DeclarationDueDateHistory,
        DeclarationStatusHistory,
    )
    from app.models.obligation import ClientObligation, ObligationCatalog
    from app.models.tax_period import TaxPeriod
    from app.models.tax_schedule import TaxScheduleEntry, TaxScheduleSource, TaxScheduleVersion

    session = session_factory()
    try:
        session.execute(delete(TaskComment).where(TaskComment.tenant_id == tenant_id))
        session.execute(delete(TaskChangeHistory).where(TaskChangeHistory.tenant_id == tenant_id))
        session.execute(delete(OperationalTask).where(OperationalTask.tenant_id == tenant_id))
        session.execute(delete(TaskGenerationRun).where(TaskGenerationRun.tenant_id == tenant_id))
        session.execute(
            delete(DeclarationDueDateHistory).where(DeclarationDueDateHistory.tenant_id == tenant_id)
        )
        session.execute(
            delete(DeclarationStatusHistory).where(DeclarationStatusHistory.tenant_id == tenant_id)
        )
        session.execute(delete(Declaration).where(Declaration.tenant_id == tenant_id))
        session.execute(delete(TaxPeriod).where(TaxPeriod.tenant_id == tenant_id))
        session.execute(delete(TaxScheduleEntry).where(TaxScheduleEntry.tenant_id == tenant_id))
        session.execute(delete(TaxScheduleVersion).where(TaxScheduleVersion.tenant_id == tenant_id))
        session.execute(delete(TaxScheduleSource).where(TaxScheduleSource.tenant_id == tenant_id))
        session.execute(delete(ClientObligation).where(ClientObligation.tenant_id == tenant_id))
        session.execute(delete(Client).where(Client.tenant_id == tenant_id))
        session.execute(delete(ObligationCatalog).where(ObligationCatalog.id == obligation_id))
        session.execute(delete(AuditLog).where(AuditLog.tenant_id == tenant_id))
        session.execute(delete(User).where(User.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()
    finally:
        session.close()
