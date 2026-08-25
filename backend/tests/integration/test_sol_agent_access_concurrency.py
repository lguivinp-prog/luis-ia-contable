"""Fase 5, Bloque 5.2 (ver docs/phase5/block5_2/, Domain A.1 secciones 21/34
y Domain A.2 Gate 3/4): concurrencia real con DOS conexiones PostgreSQL
independientes contra el mismo `test_engine` -- mismo patron que
test_tax_payment_concurrency.py / test_operational_task_concurrency.py.
Nunca SQLite, nunca mock, nunca solo `sleep` como sustituto de
sincronizacion real -- el bloqueo real de Postgres sobre el indice unico
parcial es lo que fuerza la serializacion entre hilos."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password, hash_session_token
from app.models.client import Client
from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_agent_access import SolAccessSession, SolAgent, SolAgentJob
from app.models.sol_vault import SolVault, SolVaultDataKey
from app.models.tenant import Tenant
from app.models.user import User
from app.services.ruc_validator import compute_check_digit


@pytest.fixture()
def session_factory(test_engine):
    return sessionmaker(bind=test_engine, future=True)


def _fake_ruc() -> str:
    base8 = f"{uuid.uuid4().int % 10**8:08d}"
    first_ten = "20" + base8
    return first_ten + str(compute_check_digit(first_ten))


def _commit_tenant_user(session_factory) -> dict:
    session = session_factory()
    try:
        tenant = Tenant(name="Tenant concurrencia acceso SOL")
        session.add(tenant)
        session.flush()
        admin = User(
            tenant_id=tenant.id,
            email=f"admin-solconc-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Admin concurrencia acceso SOL",
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


def _commit_credential(session_factory, tenant_id, admin_id) -> uuid.UUID:
    """Crea cliente + credencial reales y COMMITEADOS (bytes cifrados dummy
    -- estas pruebas ejercitan DDL/concurrencia, no criptografia real).
    `sol_vaults` tiene `UNIQUE(tenant_id)` -- reutiliza la boveda del
    tenant si una llamada previa ya la creo (varios clientes/credenciales
    pueden compartir la misma boveda de tenant)."""
    session = session_factory()
    try:
        client = Client(
            tenant_id=tenant_id,
            ruc=_fake_ruc(),
            business_name="Cliente concurrencia acceso SOL",
            person_type="juridica",
            ruc_status="ACTIVO",
            fiscal_address_condition="HABIDO",
            tax_regime="GENERAL",
            service_status="activo",
        )
        session.add(client)
        session.flush()

        now = datetime.now(UTC)
        vault = session.execute(
            select(SolVault).where(SolVault.tenant_id == tenant_id)
        ).scalar_one_or_none()
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
            authorization_basis="Autorizacion de prueba (concurrencia)",
            authorized_at=now,
            created_by=admin_id,
        )
        session.add(credential)
        session.flush()
        session.commit()
        return client.id, credential.id
    finally:
        session.close()


def _commit_session(session_factory, tenant_id, client_id, credential_id, admin_id, *, status="CREATED"):
    session = session_factory()
    try:
        row = SolAccessSession(
            tenant_id=tenant_id,
            client_id=client_id,
            credential_id=credential_id,
            requested_by_user_id=admin_id,
            scope="AUTHENTICATE_TO_LANDING_ONLY",
            status=status,
            idempotency_key=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        session.add(row)
        session.flush()
        session.commit()
        return row.id
    finally:
        session.close()


def _commit_agent(session_factory, tenant_id) -> uuid.UUID:
    session = session_factory()
    try:
        agent = SolAgent(
            tenant_id=tenant_id,
            display_name="Agente concurrencia",
            certificate_fingerprint=f"fp-conc-{uuid.uuid4().hex}",
            paired_at=datetime.now(UTC),
        )
        session.add(agent)
        session.flush()
        session.commit()
        return agent.id
    finally:
        session.close()


def _commit_job(session_factory, tenant_id, session_id, agent_id, *, status, credential_delivered_at=None):
    session = session_factory()
    try:
        job = SolAgentJob(
            tenant_id=tenant_id,
            session_id=session_id,
            agent_id=agent_id,
            status=status,
            job_token_hash=hash_session_token(uuid.uuid4().hex),
            credential_delivered_at=credential_delivered_at,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        session.add(job)
        session.flush()
        session.commit()
        return job.id
    finally:
        session.close()


def _cleanup(session_factory, tenant_id: uuid.UUID) -> None:
    session = session_factory()
    try:
        session.execute(delete(SolAgentJob).where(SolAgentJob.tenant_id == tenant_id))
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


def test_two_connection_active_job_per_session_race(session_factory) -> None:
    """Dos hilos, DOS conexiones reales, intentan INSERTAR simultaneamente un
    job PENDING/CLAIMED para la MISMA sesion. El indice unico parcial
    `ux_sol_agent_jobs_active_per_session` debe forzar que Postgres bloquee
    la segunda insercion hasta que la primera resuelva, y solo UNA gane."""
    setup = _commit_tenant_user(session_factory)
    client_id, credential_id = _commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    session_id = _commit_session(session_factory, setup["tenant_id"], client_id, credential_id, setup["admin_id"])
    agent_a_id = _commit_agent(session_factory, setup["tenant_id"])
    agent_b_id = _commit_agent(session_factory, setup["tenant_id"])

    a_inserted = threading.Event()
    b_attempting = threading.Event()
    a_may_commit = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def worker_a():
        session = session_factory()
        try:
            job = SolAgentJob(
                tenant_id=setup["tenant_id"],
                session_id=session_id,
                agent_id=agent_a_id,
                status="PENDING",
                job_token_hash=hash_session_token(uuid.uuid4().hex),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            session.add(job)
            session.flush()
            a_inserted.set()
            assert b_attempting.wait(timeout=5), "B nunca intento su propia insercion"
            a_may_commit.wait(timeout=5)
            session.commit()
            results["a"] = job.id
        except BaseException as exc:  # noqa: BLE001
            errors["a"] = exc
            session.rollback()
        finally:
            session.close()

    def worker_b():
        assert a_inserted.wait(timeout=5), "A nunca inserto su job"
        session = session_factory()
        try:
            job = SolAgentJob(
                tenant_id=setup["tenant_id"],
                session_id=session_id,
                agent_id=agent_b_id,
                status="CLAIMED",
                job_token_hash=hash_session_token(uuid.uuid4().hex),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            session.add(job)
            b_attempting.set()
            # Bloquea de verdad contra el indice unico parcial hasta que A
            # resuelva (commit o rollback) su propia insercion en conflicto.
            session.flush()
            session.commit()
            results["b"] = job.id
        except BaseException as exc:  # noqa: BLE001
            errors["b"] = exc
            session.rollback()
        finally:
            session.close()

    thread_a = threading.Thread(target=worker_a)
    thread_b = threading.Thread(target=worker_b)
    thread_a.start()
    thread_b.start()
    assert b_attempting.wait(timeout=5), "el hilo B nunca comenzo su intento de insercion"
    a_may_commit.set()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    try:
        succeeded = [k for k in ("a", "b") if k in results]
        failed = [k for k in ("a", "b") if k in errors]
        assert len(succeeded) == 1, f"se esperaba exactamente 1 job activo aceptado: {results} / {errors}"
        assert len(failed) == 1
        assert isinstance(errors[failed[0]], IntegrityError)
        print("TWO_CONNECTION_ACTIVE_SESSION_JOB_RACE=PASS")
    finally:
        _cleanup(session_factory, setup["tenant_id"])


def test_two_connection_active_job_per_agent_race(session_factory) -> None:
    """Dos sesiones DISTINTAS (para no chocar con el indice de sesion),
    mismo agente -- el indice unico parcial `ux_sol_agent_jobs_active_per_agent`
    debe permitir que como mucho UNO de los dos jobs activos gane."""
    setup = _commit_tenant_user(session_factory)
    client_a_id, credential_a_id = _commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    client_b_id, credential_b_id = _commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    session_a_id = _commit_session(
        session_factory, setup["tenant_id"], client_a_id, credential_a_id, setup["admin_id"]
    )
    session_b_id = _commit_session(
        session_factory, setup["tenant_id"], client_b_id, credential_b_id, setup["admin_id"]
    )
    agent_id = _commit_agent(session_factory, setup["tenant_id"])

    a_inserted = threading.Event()
    b_attempting = threading.Event()
    a_may_commit = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def worker_a():
        session = session_factory()
        try:
            job = SolAgentJob(
                tenant_id=setup["tenant_id"],
                session_id=session_a_id,
                agent_id=agent_id,
                status="PENDING",
                job_token_hash=hash_session_token(uuid.uuid4().hex),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            session.add(job)
            session.flush()
            a_inserted.set()
            assert b_attempting.wait(timeout=5), "B nunca intento su propia insercion"
            a_may_commit.wait(timeout=5)
            session.commit()
            results["a"] = job.id
        except BaseException as exc:  # noqa: BLE001
            errors["a"] = exc
            session.rollback()
        finally:
            session.close()

    def worker_b():
        assert a_inserted.wait(timeout=5), "A nunca inserto su job"
        session = session_factory()
        try:
            job = SolAgentJob(
                tenant_id=setup["tenant_id"],
                session_id=session_b_id,
                agent_id=agent_id,
                status="CLAIMED",
                job_token_hash=hash_session_token(uuid.uuid4().hex),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            session.add(job)
            b_attempting.set()
            session.flush()
            session.commit()
            results["b"] = job.id
        except BaseException as exc:  # noqa: BLE001
            errors["b"] = exc
            session.rollback()
        finally:
            session.close()

    thread_a = threading.Thread(target=worker_a)
    thread_b = threading.Thread(target=worker_b)
    thread_a.start()
    thread_b.start()
    assert b_attempting.wait(timeout=5), "el hilo B nunca comenzo su intento de insercion"
    a_may_commit.set()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    try:
        succeeded = [k for k in ("a", "b") if k in results]
        failed = [k for k in ("a", "b") if k in errors]
        assert len(succeeded) == 1, f"se esperaba exactamente 1 job activo aceptado: {results} / {errors}"
        assert len(failed) == 1
        assert isinstance(errors[failed[0]], IntegrityError)
        print("TWO_CONNECTION_ACTIVE_AGENT_JOB_RACE=PASS")
    finally:
        _cleanup(session_factory, setup["tenant_id"])


def test_two_connection_credential_delivery_race(session_factory) -> None:
    """LA PRUEBA CRITICA (Domain A.2 Gate 3/4, encargo de Domain B seccion
    52.C): dos jobs HISTORICOS distintos de la MISMA sesion intentan, con
    DOS conexiones Postgres reales, marcar `credential_delivered_at`
    simultaneamente via la misma sentencia UPDATE congelada en
    BLOCK5_2_DATA_MODEL_DRAFT.md ('UPDATE ... WHERE id=:job_id AND
    credential_delivered_at IS NULL'). El indice unico parcial
    `ux_sol_agent_jobs_one_delivery_per_session` debe garantizar que, bajo
    concurrencia real, como MUCHO uno de los dos UPDATE gane -- nunca
    ambos, sin importar el orden de llegada."""
    setup = _commit_tenant_user(session_factory)
    client_id, credential_id = _commit_credential(session_factory, setup["tenant_id"], setup["admin_id"])
    session_id = _commit_session(session_factory, setup["tenant_id"], client_id, credential_id, setup["admin_id"])
    agent_a_id = _commit_agent(session_factory, setup["tenant_id"])
    agent_b_id = _commit_agent(session_factory, setup["tenant_id"])

    # Ambos jobs ya existen, COMPLETADOS/FALLIDOS -- ninguno "activo"
    # (PENDING/CLAIMED), asi que el indice de "job activo por sesion" no
    # interfiere; ambos con credential_delivered_at NULL todavia, listos
    # para la carrera de entrega.
    job_a_id = _commit_job(session_factory, setup["tenant_id"], session_id, agent_a_id, status="COMPLETED")
    job_b_id = _commit_job(session_factory, setup["tenant_id"], session_id, agent_b_id, status="FAILED")

    _UPDATE_SQL = text(
        "UPDATE sol_agent_jobs SET credential_delivered_at = now() "
        "WHERE id = :job_id AND credential_delivered_at IS NULL"
    )

    a_updated = threading.Event()
    b_attempting = threading.Event()
    a_may_commit = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def worker_a():
        session = session_factory()
        try:
            session.execute(_UPDATE_SQL, {"job_id": job_a_id})
            a_updated.set()
            assert b_attempting.wait(timeout=5), "B nunca intento su propio UPDATE"
            a_may_commit.wait(timeout=5)
            session.commit()
            results["a"] = "delivered"
        except BaseException as exc:  # noqa: BLE001
            errors["a"] = exc
            session.rollback()
        finally:
            session.close()

    def worker_b():
        assert a_updated.wait(timeout=5), "A nunca ejecuto su propio UPDATE"
        session = session_factory()
        try:
            b_attempting.set()
            # Bloquea de verdad contra el indice unico parcial (misma
            # session_id, ambos intentando credential_delivered_at IS NOT
            # NULL) hasta que A resuelva su transaccion.
            session.execute(_UPDATE_SQL, {"job_id": job_b_id})
            session.commit()
            results["b"] = "delivered"
        except BaseException as exc:  # noqa: BLE001
            errors["b"] = exc
            session.rollback()
        finally:
            session.close()

    thread_a = threading.Thread(target=worker_a)
    thread_b = threading.Thread(target=worker_b)
    thread_a.start()
    thread_b.start()
    assert b_attempting.wait(timeout=5), "el hilo B nunca comenzo su intento de UPDATE"
    a_may_commit.set()
    thread_a.join(timeout=15)
    thread_b.join(timeout=15)

    try:
        succeeded = [k for k in ("a", "b") if k in results]
        failed = [k for k in ("a", "b") if k in errors]
        assert len(succeeded) == 1, (
            f"MAX_CREDENTIAL_DELIVERIES_PER_SESSION=1 violado: {results} / {errors}"
        )
        assert len(failed) == 1
        assert isinstance(errors[failed[0]], IntegrityError)

        verify = session_factory()
        try:
            delivered = (
                verify.execute(
                    select(SolAgentJob.id, SolAgentJob.credential_delivered_at).where(
                        SolAgentJob.session_id == session_id
                    )
                )
                .all()
            )
        finally:
            verify.close()
        delivered_count = sum(1 for _id, ts in delivered if ts is not None)
        assert delivered_count == 1, (
            f"exactamente 1 job de la sesion debe tener credential_delivered_at poblado: {delivered}"
        )
        print("TWO_CONNECTION_CREDENTIAL_DELIVERY_RACE=PASS")
    finally:
        _cleanup(session_factory, setup["tenant_id"])
