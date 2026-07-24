"""Concurrencia real del invariante de ultimo-admin (Bloque 4, ADR-0051,
Seccion 3 del plan): dos conexiones reales independientes contra el mismo
`test_engine`, sincronizadas con `threading.Event` (nunca solo `sleep` como
sustituto de sincronizacion real) -- mismo patron que
`test_tax_payment_concurrency.py` (ADR-0042). Verifica que, aunque dos admins
intenten simultaneamente dejar al tenant sin ningun admin activo, el bloqueo
determinista (`SELECT ... FOR UPDATE ... ORDER BY id`) garantiza que
exactamente uno de los dos sobrevive activo, nunca cero."""

import threading
import time
import uuid

from sqlalchemy.orm import sessionmaker

from app.core import clock
from app.core.security import hash_password
from app.models.tenant import Tenant
from app.models.user import User
from app.services import user_service
from app.services.user_service import LastActiveAdminError


def _build_tenant_with_two_admins(session_factory) -> dict:
    session = session_factory()
    try:
        tenant = Tenant(name="Tenant concurrencia ultimo-admin")
        session.add(tenant)
        session.flush()

        admin_x = User(
            tenant_id=tenant.id,
            email=f"admin-x-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Admin X",
            password_hash=hash_password("TestPassword123!"),
            role="admin",
            is_active=True,
        )
        admin_y = User(
            tenant_id=tenant.id,
            email=f"admin-y-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Admin Y",
            password_hash=hash_password("TestPassword123!"),
            role="admin",
            is_active=True,
        )
        session.add_all([admin_x, admin_y])
        session.commit()
        return {"tenant_id": tenant.id, "admin_x_id": admin_x.id, "admin_y_id": admin_y.id}
    finally:
        session.close()


def _cleanup(session_factory, tenant_id: uuid.UUID) -> None:
    from sqlalchemy import delete

    session = session_factory()
    try:
        session.execute(delete(User).where(User.tenant_id == tenant_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()
    finally:
        session.close()


def test_two_admins_deactivate_each_other_concurrently_only_one_survives(test_engine) -> None:
    session_factory = sessionmaker(bind=test_engine, future=True)
    setup = _build_tenant_with_two_admins(session_factory)
    tenant_id = setup["tenant_id"]
    x_id, y_id = setup["admin_x_id"], setup["admin_y_id"]

    a_locked = threading.Event()
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def worker_deactivate_y_as_x() -> None:
        session = session_factory()
        try:
            actor = session.get(User, x_id)
            a_locked.set()
            time.sleep(0.3)  # da tiempo a que el hilo B quede realmente bloqueado
            user_service.deactivate_user(
                session, tenant_id, actor, y_id, now=clock.utcnow(), reason="Hilo A"
            )
            session.commit()
            results["a_outcome"] = "deactivated"
        except LastActiveAdminError:
            session.rollback()
            results["a_outcome"] = "blocked"
        except BaseException as exc:  # noqa: BLE001 -- se reporta en el hilo principal
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    def worker_deactivate_x_as_y() -> None:
        a_locked.wait(timeout=5)
        session = session_factory()
        try:
            actor = session.get(User, y_id)
            user_service.deactivate_user(
                session, tenant_id, actor, x_id, now=clock.utcnow(), reason="Hilo B"
            )
            session.commit()
            results["b_outcome"] = "deactivated"
        except LastActiveAdminError:
            session.rollback()
            results["b_outcome"] = "blocked"
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    thread_a = threading.Thread(target=worker_deactivate_y_as_x)
    thread_b = threading.Thread(target=worker_deactivate_x_as_y)
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    assert not errors, f"Errores inesperados en los hilos: {errors}"
    assert {results.get("a_outcome"), results.get("b_outcome")} == {"deactivated", "blocked"}

    verify_session = session_factory()
    try:
        active_admins = (
            verify_session.query(User)
            .filter(User.tenant_id == tenant_id, User.role == "admin", User.is_active.is_(True))
            .count()
        )
    finally:
        verify_session.close()
    assert active_admins == 1

    _cleanup(session_factory, tenant_id)


def test_two_admins_demote_each_other_concurrently_only_one_survives(test_engine) -> None:
    session_factory = sessionmaker(bind=test_engine, future=True)
    setup = _build_tenant_with_two_admins(session_factory)
    tenant_id = setup["tenant_id"]
    x_id, y_id = setup["admin_x_id"], setup["admin_y_id"]

    a_locked = threading.Event()
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def worker_demote_y_as_x() -> None:
        session = session_factory()
        try:
            actor = session.get(User, x_id)
            a_locked.set()
            time.sleep(0.3)
            user_service.change_role(
                session, tenant_id, actor, y_id, new_role="contador", reason="Hilo A degrada a Y"
            )
            session.commit()
            results["a_outcome"] = "demoted"
        except LastActiveAdminError:
            session.rollback()
            results["a_outcome"] = "blocked"
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    def worker_demote_x_as_y() -> None:
        a_locked.wait(timeout=5)
        session = session_factory()
        try:
            actor = session.get(User, y_id)
            user_service.change_role(
                session, tenant_id, actor, x_id, new_role="asistente", reason="Hilo B degrada a X"
            )
            session.commit()
            results["b_outcome"] = "demoted"
        except LastActiveAdminError:
            session.rollback()
            results["b_outcome"] = "blocked"
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    thread_a = threading.Thread(target=worker_demote_y_as_x)
    thread_b = threading.Thread(target=worker_demote_x_as_y)
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    assert not errors, f"Errores inesperados en los hilos: {errors}"
    assert {results.get("a_outcome"), results.get("b_outcome")} == {"demoted", "blocked"}

    verify_session = session_factory()
    try:
        active_admins = (
            verify_session.query(User)
            .filter(User.tenant_id == tenant_id, User.role == "admin", User.is_active.is_(True))
            .count()
        )
    finally:
        verify_session.close()
    assert active_admins == 1

    _cleanup(session_factory, tenant_id)
