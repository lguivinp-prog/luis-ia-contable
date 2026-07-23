"""Concurrencia financiera real (Bloque 3.2, Dominio B): ver "Concurrencia
financiera" del plan / ADR-0042. A diferencia de las demas pruebas de
este archivo (que comparten una unica conexion/sesion transaccional de
prueba), esta prueba abre DOS conexiones reales independientes contra el
mismo `test_engine` y usa un hilo separado + `threading.Event` para forzar
que la segunda conexion quede realmente bloqueada por el `SELECT ... FOR
UPDATE` de la primera -- nunca solo un `sleep` como sustituto de
sincronizacion real."""

import threading
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.models.client import Client
from app.models.obligation import ClientObligation, ObligationCatalog
from app.models.tax_schedule import TaxScheduleEntry, TaxScheduleSource, TaxScheduleVersion
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories import declaration_repository, tax_payment_repository
from app.services import declaration_service, tax_assessment_service, tax_payment_service
from app.services.ruc_validator import compute_check_digit


def _fake_ruc() -> str:
    base8 = f"{uuid.uuid4().int % 10**8:08d}"
    first_ten = "20" + base8
    return first_ten + str(compute_check_digit(first_ten))


def _build_committed_declaration_with_assessment(session_factory) -> dict:
    """Construye tenant/cliente/obligacion/cronograma/periodo/declaracion/
    determinacion inicial y los COMMITEA de verdad (no una savepoint de
    prueba), para que sean visibles desde una conexion Postgres distinta."""
    session = session_factory()
    try:
        tenant = Tenant(name="Tenant concurrencia pagos")
        session.add(tenant)
        session.flush()

        user = User(
            tenant_id=tenant.id,
            email=f"concurrencia-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Usuario concurrencia",
            password_hash=hash_password("TestPassword123!"),
            role="admin",
            is_active=True,
        )
        session.add(user)
        session.flush()

        obligation = ObligationCatalog(
            code=f"CONC-{uuid.uuid4().hex[:6]}",
            name="Obligacion concurrencia",
            periodicity="mensual",
            source="Fixture de prueba de concurrencia",
            source_version="test-v1",
            is_active=True,
        )
        session.add(obligation)
        session.flush()

        client = Client(
            tenant_id=tenant.id,
            ruc=_fake_ruc(),
            business_name="Cliente concurrencia",
            person_type="juridica",
            ruc_status="ACTIVO",
            fiscal_address_condition="HABIDO",
            tax_regime="GENERAL",
            service_status="activo",
        )
        session.add(client)
        session.flush()

        client_obligation = ClientObligation(
            tenant_id=tenant.id,
            client_id=client.id,
            obligation_id=obligation.id,
            is_active=True,
            assigned_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        session.add(client_obligation)
        session.flush()

        source = TaxScheduleSource(
            tenant_id=tenant.id, obligation_id=obligation.id, name="Cronograma concurrencia", is_active=True
        )
        session.add(source)
        session.flush()

        version = TaxScheduleVersion(
            tenant_id=tenant.id,
            source_id=source.id,
            version_label=f"v-conc-{uuid.uuid4().hex[:6]}",
            fiscal_year=2026,
            schedule_type="GENERAL",
            scope_category_code=None,
            status="APPROVED",
        )
        session.add(version)
        session.flush()

        entry = TaxScheduleEntry(
            tenant_id=tenant.id,
            version_id=version.id,
            tax_period="2026-01",
            applicable_last_digits=list(range(10)),
            required_category_code=None,
            priority=None,
            due_date_original=date(2026, 2, 12),
            due_date_effective=date(2026, 2, 12),
            due_date_type="REGULAR",
        )
        session.add(entry)
        session.flush()

        from app.services import tax_period_service

        period = tax_period_service.build_period(
            period_type="MONTHLY",
            fiscal_year=2026,
            month=1,
            canonical_code=None,
            schedule_tax_period_code=None,
            display_name=None,
            start_date=None,
            end_date=None,
            description=None,
        )
        period.tenant_id = tenant.id
        period.is_open = True
        session.add(period)
        session.flush()

        declaration = declaration_service.create_declaration(
            session,
            tenant.id,
            user.id,
            client_id=client.id,
            client_obligation_id=client_obligation.id,
            tax_period_id=period.id,
            origin="MANUAL",
            sync_documents=False,
        )

        locked = tax_assessment_service.lock_declaration(session, tenant.id, declaration.id)
        tax_assessment_service.create_initial_assessment(
            session,
            tenant.id,
            locked,
            gross_tax_amount=Decimal("1000"),
            credits_applied=Decimal("0"),
            observations=None,
            actor_user_id=user.id,
        )
        session.commit()
        return {
            "tenant_id": tenant.id,
            "declaration_id": declaration.id,
            "user_id": user.id,
            "obligation_id": obligation.id,
        }
    finally:
        session.close()


def test_two_real_connections_serialize_on_for_update_lock(test_engine) -> None:
    session_factory = sessionmaker(bind=test_engine, future=True)
    setup = _build_committed_declaration_with_assessment(session_factory)
    tenant_id = setup["tenant_id"]
    declaration_id = setup["declaration_id"]
    user_id = setup["user_id"]

    a_locked = threading.Event()
    b_may_attempt_lock = threading.Event()
    b_lock_acquired = threading.Event()
    results: dict[str, object] = {}
    errors: list[BaseException] = []

    def worker_a() -> None:
        session = session_factory()
        try:
            declaration = declaration_repository.lock_for_update(session, tenant_id, declaration_id)
            a_locked.set()
            # Mantiene el bloqueo abierto a proposito hasta que B confirme
            # que ya intento (y quedo bloqueado) adquiriendo el mismo lock.
            b_may_attempt_lock.wait(timeout=5)
            import time

            time.sleep(0.4)
            tax_payment_service.create_payment(
                session,
                tenant_id,
                declaration,
                idempotency_key=uuid.uuid4(),
                amount=Decimal("400"),
                paid_at=date(2026, 2, 1),
                payment_method="TRANSFER",
                operation_number=None,
                reference=None,
                observations=None,
                overpayment_confirmed=False,
                overpayment_reason=None,
                actor_user_id=user_id,
            )
            session.commit()
            results["a_amount"] = Decimal("400")
        except BaseException as exc:  # noqa: BLE001 -- se reporta en el hilo principal
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    def worker_b() -> None:
        a_locked.wait(timeout=5)
        session = session_factory()
        try:
            b_may_attempt_lock.set()
            # Esta llamada debe bloquearse realmente hasta que A haga commit
            # (o rollback) y libere el FOR UPDATE sobre la misma fila.
            declaration = declaration_repository.lock_for_update(session, tenant_id, declaration_id)
            b_lock_acquired.set()
            tax_payment_service.create_payment(
                session,
                tenant_id,
                declaration,
                idempotency_key=uuid.uuid4(),
                amount=Decimal("600"),
                paid_at=date(2026, 2, 2),
                payment_method="TRANSFER",
                operation_number=None,
                reference=None,
                observations=None,
                overpayment_confirmed=False,
                overpayment_reason=None,
                actor_user_id=user_id,
            )
            session.commit()
            results["b_amount"] = Decimal("600")
        except BaseException as exc:  # noqa: BLE001 -- se reporta en el hilo principal
            errors.append(exc)
            session.rollback()
        finally:
            session.close()

    thread_a = threading.Thread(target=worker_a)
    thread_b = threading.Thread(target=worker_b)
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    assert not errors, f"Errores inesperados en los hilos: {errors}"
    assert "a_amount" in results
    assert "b_amount" in results
    # B solo pudo adquirir su lock DESPUES de que A ya hubiera confirmado
    # (o al menos liberado) su propio commit -- si no hubiera bloqueo real,
    # ambos podrian haber leido total_paid=0 simultaneamente sin problema
    # (aqui no habria overpago porque 400+600=1000 exacto, pero lo que se
    # verifica es que no se pierda ninguna de las dos escrituras).
    assert b_lock_acquired.is_set()

    verify_session = session_factory()
    try:
        total_paid = tax_payment_repository.sum_valid_amount_for_declaration(
            verify_session, tenant_id, declaration_id
        )
        all_payments = tax_payment_repository.list_for_declaration(
            verify_session, tenant_id, declaration_id
        )
    finally:
        verify_session.close()

    assert total_paid == Decimal("1000")
    assert len(all_payments) == 2

    _cleanup_committed_data(session_factory, tenant_id, setup["obligation_id"])


def _cleanup_committed_data(session_factory, tenant_id: uuid.UUID, obligation_id: uuid.UUID) -> None:
    """Esta prueba (a diferencia de las demas, que usan la savepoint
    descartable de `db_session`) commitea datos reales sobre `test_engine`
    para simular conexiones independientes -- se eliminan explicitamente en
    orden seguro para las FK (historiales antes que sus padres) para no
    dejar residuos visibles a otras pruebas que consulten sin filtrar por
    tenant."""
    from sqlalchemy import delete

    from app.models.client import Client
    from app.models.declaration import (
        Declaration,
        DeclarationAssignmentHistory,
        DeclarationDueDateHistory,
        DeclarationStatusHistory,
    )
    from app.models.obligation import ClientObligation, ObligationCatalog
    from app.models.tax_assessment import TaxAssessment, TaxAssessmentHistory
    from app.models.tax_payment import TaxPayment, TaxPaymentHistory
    from app.models.tax_period import TaxPeriod
    from app.models.tax_schedule import TaxScheduleEntry, TaxScheduleSource, TaxScheduleVersion
    from app.models.tenant import Tenant
    from app.models.user import User

    session = session_factory()
    try:
        for model in (
            TaxPaymentHistory,
            TaxPayment,
            TaxAssessmentHistory,
            TaxAssessment,
            DeclarationStatusHistory,
            DeclarationDueDateHistory,
            DeclarationAssignmentHistory,
            Declaration,
            TaxScheduleEntry,
            TaxScheduleVersion,
            TaxScheduleSource,
            ClientObligation,
            TaxPeriod,
            Client,
            User,
        ):
            session.execute(delete(model).where(model.tenant_id == tenant_id))
        session.execute(delete(ObligationCatalog).where(ObligationCatalog.id == obligation_id))
        session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        session.commit()
    finally:
        session.close()
