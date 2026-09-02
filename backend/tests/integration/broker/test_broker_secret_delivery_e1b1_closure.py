"""Fase 5, Bloque 5.2, Domain E1-B.1 -- cierre de seguridad final de entrega
de credencial SOL. Tres gates:

- Gate A (transaction ownership): `authorize_credential_delivery` ahora abre
  y posee su propia sesion dedicada (`session_factory()` interno) -- esta
  prueba demuestra que una mutacion no relacionada, ya pendiente en la
  sesion PROPIA del llamador, nunca puede ser comprometida como efecto
  secundario del commit de autorizacion.
- Gate B (secret lease timeout): el lease de conexion ahora tiene un plazo
  real y acotado (`ConnectionRegistry._secret_lease_timeout_seconds`,
  poblado desde `sol_broker_read_timeout_seconds`) -- esta prueba demuestra
  que un lease ABANDONADO (su poseedor nunca llega a `release_lease`) se
  autopurga tras ese plazo, sin bloquear un `HELLO` de reemplazo para
  siempre.
- Gate C (cierre de evidencia): dos huecos de cobertura reales identificados
  al mapear cada propiedad exigida a un nombre de prueba concreto (nunca
  solo `TEST_MATRIX_A_AD=PASS`) --
  (1) `RETIRED_DATA_KEY_VERSION_SUPPORTED` nunca tuvo una prueba explicita
  de que `deliver_credential` descifra correctamente una credencial cuya
  `data_key_version` senala una `SolVaultDataKey` YA RETIRED (rotada) --
  solo el codigo defensivo. Esta prueba construye ese escenario con los
  primitivos criptograficos REALES de produccion (nunca mocks) y prueba la
  entrega extremo a extremo.
  (2) `AUTHORIZATION_REQUIRES_SESSION_REQUESTER`/
  `AUTHORIZATION_REQUIRES_SOL_SESSION_CREATE`/
  `AUTHORIZATION_REQUIRES_SOL_CREDENTIAL_READ` tampoco tenian ninguna
  prueba dedicada -- solo inspeccion de codigo. Estas dos pruebas cierran
  ese hueco directamente contra `authorize_credential_delivery`."""

from __future__ import annotations

import uuid

from app.broker.connection_registry import ConnectionRegistry
from app.broker.dispatch import dispatch_job_to_agent
from app.broker.server import BrokerServer
from app.core import clock
from app.core import sol_vault_crypto as crypto
from app.core.security import hash_password
from app.core.sol_vault_registry import vault_unlock_registry
from app.models.client_sol_credential import ClientSolCredential
from app.models.sol_vault import SolVault
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories import sol_vault_repository as vault_repo
from app.services import sol_agent_credential_delivery_service as delivery_service
from app.services import sol_vault_service
from tests.integration.broker._secret_delivery_helpers import (
    TEST_SOL_PASSWORD_DO_NOT_USE,
    TEST_SOL_USER,
    commit_client,
    commit_sol_access_session,
    get_user_row,
)
from tests.integration.broker.agent_test_client import AgentTestClient
from tests.integration.broker.conftest import cleanup_tenant, create_pairing_over_service


def _create_extra_user_same_tenant(session_factory, *, tenant_id: uuid.UUID, role: str) -> uuid.UUID:
    """Segundo usuario del MISMO tenant (nunca uno nuevo -- las
    comprobaciones de `authorize_credential_delivery` son intra-tenant),
    con el `role` exacto que la prueba necesita para el borde RBAC."""
    session = session_factory()
    try:
        user = User(
            tenant_id=tenant_id,
            email=f"extra-e1b1-{uuid.uuid4().hex[:8]}@luisiacontable-tests.dev",
            full_name="Usuario adicional pruebas E1-B.1",
            password_hash=hash_password("TestPassword123!"),
            role=role,
            is_active=True,
        )
        session.add(user)
        session.flush()
        session.commit()
        return user.id
    finally:
        session.close()


def _pair_agent(session_factory, running_broker, tenant_id, admin_id) -> AgentTestClient:
    from cryptography.hazmat.primitives import serialization

    pairing_id, pairing_code = create_pairing_over_service(
        session_factory, tenant_id=tenant_id, requested_by_user_id=admin_id
    )
    agent = AgentTestClient(
        ca_certificate_pem=running_broker.ca.certificate.public_bytes(serialization.Encoding.PEM),
        pairing_base_url=f"https://127.0.0.1:{running_broker.pairing_bound_port}",
        operational_base_url=f"https://127.0.0.1:{running_broker.operational_bound_port}",
    )
    assert agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code).status_code == 200
    assert agent.hello().status_code == 200
    return agent


def _dispatch_and_claim(session_factory, running_broker, setup, agent) -> uuid.UUID:
    dispatch_session = session_factory()
    try:
        job_id = dispatch_job_to_agent(
            dispatch_session,
            tenant_id=setup["tenant_id"],
            session_id=setup["session_id"],
            agent_id=agent.agent_id,
            pending_dispatch=running_broker.pending_dispatch,
        )
        dispatch_session.commit()
    finally:
        dispatch_session.close()
    assert agent.claim(job_id).status_code == 200
    return job_id


def test_gate_a_authorization_commit_never_commits_unrelated_caller_session_state(
    session_factory, running_broker, e1b_setup
):
    """Gate A: el llamador abre SU PROPIA sesion y deja en ella una
    mutacion no relacionada pendiente (un `Tenant` nuevo, `flush()`eado
    pero JAMAS comprometido por el llamador). Se invoca
    `authorize_credential_delivery` pasando el `session_factory` (nunca la
    sesion del llamador) -- la funcion abre y posee su PROPIA sesion,
    completamente independiente, en su propia conexion de base de datos.
    Tras la autorizacion, se cierra la sesion del llamador SIN commit
    (equivalente a un rollback implicito) y se confirma con una sesion de
    verificacion fresca que:

    1. el `Tenant` no relacionado JAMAS se persistio (la unica forma en
       que pudiera existir es si el commit interno de autorizacion
       hubiera comprometido, por error, la sesion del llamador en vez de
       la suya propia); y
    2. la autorizacion SI tuvo exito de forma independiente (auditoria
       durable + grant activo) -- la aislacion no es "todo falla", es
       "cada sesion resuelve exactamente lo suyo".

    `AUTHORIZATION_COMMIT_CAN_COMMIT_UNRELATED_CALLER_STATE=NO`."""
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)

        unrelated_marker = f"E1B1-GATE-A-UNRELATED-{uuid.uuid4().hex}"
        caller_session = session_factory()
        try:
            unrelated_tenant = Tenant(name=unrelated_marker)
            caller_session.add(unrelated_tenant)
            caller_session.flush()
            unrelated_tenant_id = unrelated_tenant.id
            assert unrelated_tenant_id is not None  # sanity: realmente pendiente, con PK ya asignada

            # `authorize_credential_delivery` recibe el `session_factory`,
            # NUNCA `caller_session` -- estructuralmente no puede tocar la
            # transaccion del llamador.
            actor = get_user_row(session_factory, setup["admin_id"])
            delivery_service.authorize_credential_delivery(
                session_factory,
                grant_registry=running_broker.secret_delivery_grants,
                vault_registry=vault_unlock_registry,
                tenant_id=setup["tenant_id"],
                session_id=setup["session_id"],
                job_id=job_id,
                actor=actor,
                app_session_id=setup["app_session_id"],
            )

            # el llamador NUNCA comete su propia mutacion no relacionada
            # -- cierre sin commit, equivalente a un rollback implicito.
        finally:
            caller_session.close()

        verify_session = session_factory()
        try:
            assert verify_session.get(Tenant, unrelated_tenant_id) is None

            from app.models.audit_log import AuditAction, AuditLog
            from sqlalchemy import select

            audit_row = verify_session.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == setup["tenant_id"],
                    AuditLog.action == AuditAction.CREDENTIAL_DELIVERY_AUTHORIZED,
                    AuditLog.entity_id == job_id,
                )
            ).scalar_one_or_none()
            assert audit_row is not None
        finally:
            verify_session.close()

        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is not None

        response = agent.request_credential(job_id)
        assert response.status_code == 200
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_gate_b_abandoned_secret_lease_expires_and_never_blocks_hello_forever(session_factory, broker_ca, pairing_key):
    """Gate B: A adquiere un lease de entrega de secreto y JAMAS lo libera
    (simula un hilo poseedor atascado -- nunca llega a su `finally`). Antes
    del plazo, un HELLO de reemplazo (mismo certificado) debe rechazarse
    (`CONNECTION_BUSY`, proteccion activa). Tras avanzar el reloj
    monotonico mas alla del plazo del lease (via un `ConnectionRegistry`
    de vida corta con `secret_lease_timeout_seconds` diminuto -- mismo
    patron ya productivo de `test_C_grant_expired_by_ttl`, ningun patron
    de reloj inyectable existe todavia en este repositorio para
    reutilizar, ver `secret_delivery_grant.py`), el MISMO HELLO debe tener
    exito -- el lease abandonado se autopurga, nunca bloquea para
    siempre.

    `ABANDONED_SECRET_LEASE_EXPIRES=PASS`,
    `HELLO_BLOCKED_INDEFINITELY_BY_ABANDONED_LEASE=NO`,
    `HELLO_CAN_REPLACE_CONNECTION_DURING_ACTIVE_SECRET_LEASE=NO`,
    `HELLO_CAN_PROCEED_AFTER_SECRET_LEASE_TIMEOUT=YES`."""
    import time

    from tests.integration.broker.conftest import commit_tenant_user

    setup = commit_tenant_user(session_factory)
    try:
        short_lease_registry = ConnectionRegistry(secret_lease_timeout_seconds=0.1)
        broker = BrokerServer(
            host="127.0.0.1",
            pairing_port=0,
            operational_port=0,
            ca=broker_ca,
            session_factory=session_factory,
            connection_registry=short_lease_registry,
            idle_timeout_seconds=30.0,
            read_timeout_seconds=15.0,
        )
        broker.start()
        try:
            from cryptography.hazmat.primitives import serialization

            pairing_id, pairing_code = create_pairing_over_service(
                session_factory, tenant_id=setup["tenant_id"], requested_by_user_id=setup["admin_id"]
            )
            agent = AgentTestClient(
                ca_certificate_pem=broker.ca.certificate.public_bytes(serialization.Encoding.PEM),
                pairing_base_url=f"https://127.0.0.1:{broker.pairing_bound_port}",
                operational_base_url=f"https://127.0.0.1:{broker.operational_bound_port}",
            )
            assert agent.submit_pairing_csr(pairing_id=pairing_id, pairing_code=pairing_code).status_code == 200
            assert agent.hello().status_code == 200

            # A adquiere el lease directamente sobre el registro (simula
            # el `pin()` que haria el handler de entrega) -- deliberadamente
            # SIN llamar jamas a `release_lease` (abandono real).
            lease_token = broker.connections.pin(
                agent_id=agent.agent_id,
                connection_id=agent.connection_id,
                idle_timeout_seconds=broker.idle_timeout_seconds,
            )
            assert lease_token is not None
            assert broker.connections.get(agent_id=agent.agent_id).secret_lease_count == 1

            replacement = AgentTestClient(
                ca_certificate_pem=agent._ca_cert_pem,
                pairing_base_url=agent.pairing_base_url,
                operational_base_url=agent.operational_base_url,
            )
            replacement._client_cert_pem = agent._client_cert_pem
            replacement._client_key_pem = agent._client_key_pem
            replacement._client_cert_path = agent._client_cert_path
            replacement._client_key_path = agent._client_key_path
            replacement.agent_id = agent.agent_id

            # ANTES del plazo: proteccion activa, HELLO rechazado.
            busy = replacement.hello()
            assert busy.status_code == 409
            assert busy.json()["error"] == "CONNECTION_BUSY"

            # el lease sigue activo -- nunca se libero (abandono real).
            time.sleep(0.25)

            # DESPUES del plazo: el lease abandonado se autopurga -- HELLO
            # de reemplazo tiene exito, `NEW_REPLACES_OLD` normal resume.
            recovered = replacement.hello()
            assert recovered.status_code == 200

            record_after = broker.connections.get(agent_id=agent.agent_id)
            assert record_after.secret_lease_count == 0
            assert record_after.secret_lease_deadline is None
            assert record_after.connection_handle == replacement.connection_id
        finally:
            broker.stop()
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_gate_c_retired_data_key_version_still_decrypts_via_real_delivery(
    session_factory, running_broker, e1b_setup
):
    """Gate C: rota la data key UNA VEZ (v1 -> RETIRED, v2 -> ACTIVE; la
    propia rotacion re-cifra automaticamente la credencial de `e1b_setup` a
    v2 -- Domain 5.1, comportamiento ya productivo). Para probar el
    escenario RETIRED en si (una credencial cuya `data_key_version` senala
    la v1 YA retirada, escenario defensivo que `_decrypt_credential`
    protege pero que la migracion eager de `rotate_data_key` nunca produce
    por si sola en operacion normal), construye una SEGUNDA credencial
    DIRECTAMENTE a nivel de repositorio -- cifrada bajo el DEK real de la
    v1 (desenvuelto con los MISMOS primitivos que usa
    `sol_vault_service.rotate_data_key`, nunca un mock) y con
    `data_key_version=1` explicito. Prueba la entrega extremo a extremo
    (mTLS real, PostgreSQL real) contra esa credencial."""
    setup = e1b_setup
    try:
        rotate_session = session_factory()
        try:
            actor = get_user_row(session_factory, setup["admin_id"])
            vault_row = rotate_session.get(SolVault, setup["vault_id"])
            new_version = sol_vault_service.rotate_data_key(
                rotate_session,
                vault_unlock_registry,
                tenant_id=setup["tenant_id"],
                vault_id=setup["vault_id"],
                actor=actor,
                app_session_id=setup["app_session_id"],
                expected_version=vault_row.version,
            )
            rotate_session.commit()
        finally:
            rotate_session.close()
        assert new_version == 2

        handle = vault_unlock_registry.get_handle(
            tenant_id=setup["tenant_id"],
            vault_id=setup["vault_id"],
            user_id=setup["admin_id"],
            app_session_id=setup["app_session_id"],
        )
        entry = vault_unlock_registry.get_entry(handle)
        assert entry is not None

        # cliente NUEVO y separado -- `ux_client_sol_credentials_active_
        # per_client` permite a lo sumo una credencial no-DISABLED por
        # (tenant, client), y el cliente de `e1b_setup` ya tiene la suya
        # propia (ahora en v2 tras la rotacion).
        retired_client_id, retired_client_ruc = commit_client(session_factory, setup["tenant_id"])

        write_session = session_factory()
        try:
            old_data_key_row = vault_repo.get_data_key_by_version(
                write_session, tenant_id=setup["tenant_id"], vault_id=setup["vault_id"], data_key_version=1
            )
            assert old_data_key_row.status == "RETIRED"  # sanity: la v1 realmente quedo retirada

            old_dek_aad = crypto.aad_wrapped_data_key(
                data_key_row_id=old_data_key_row.id,
                tenant_id=setup["tenant_id"],
                vault_id=setup["vault_id"],
                data_key_version=1,
            )
            old_dek = crypto.unwrap_data_key(
                old_data_key_row.wrapped_data_key, bytes(entry.vault_root_key), old_dek_aad
            )

            retired_credential_id = uuid.uuid4()
            username_aad = crypto.aad_credential_field(
                credential_id=retired_credential_id,
                tenant_id=setup["tenant_id"],
                client_id=retired_client_id,
                field_name="sol_username",
                data_key_version=1,
            )
            password_aad = crypto.aad_credential_field(
                credential_id=retired_credential_id,
                tenant_id=setup["tenant_id"],
                client_id=retired_client_id,
                field_name="sol_password",
                data_key_version=1,
            )
            now = clock.utcnow()
            retired_credential = ClientSolCredential(
                id=retired_credential_id,
                tenant_id=setup["tenant_id"],
                client_id=retired_client_id,
                vault_id=setup["vault_id"],
                credential_kind="SECONDARY",
                sol_username_encrypted=crypto.encrypt_credential_field(TEST_SOL_USER, old_dek, username_aad),
                sol_password_encrypted=crypto.encrypt_credential_field(
                    TEST_SOL_PASSWORD_DO_NOT_USE, old_dek, password_aad
                ),
                data_key_version=1,  # deliberadamente la version YA RETIRED
                authorization_basis="Prueba Domain E1-B.1 Gate C -- version RETIRED",
                authorized_at=now,
                created_by=setup["admin_id"],
            )
            write_session.add(retired_credential)
            write_session.commit()
        finally:
            write_session.close()

        retired_session_id = commit_sol_access_session(
            session_factory,
            tenant_id=setup["tenant_id"],
            client_id=retired_client_id,
            credential_id=retired_credential_id,
            requester_id=setup["admin_id"],
        )

        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        dispatch_session = session_factory()
        try:
            job_id = dispatch_job_to_agent(
                dispatch_session,
                tenant_id=setup["tenant_id"],
                session_id=retired_session_id,
                agent_id=agent.agent_id,
                pending_dispatch=running_broker.pending_dispatch,
            )
            dispatch_session.commit()
        finally:
            dispatch_session.close()
        assert agent.claim(job_id).status_code == 200

        actor = get_user_row(session_factory, setup["admin_id"])
        delivery_service.authorize_credential_delivery(
            session_factory,
            grant_registry=running_broker.secret_delivery_grants,
            vault_registry=vault_unlock_registry,
            tenant_id=setup["tenant_id"],
            session_id=retired_session_id,
            job_id=job_id,
            actor=actor,
            app_session_id=setup["app_session_id"],
        )

        response = agent.request_credential(job_id)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ruc"] == retired_client_ruc
        assert body["sol_username"] == TEST_SOL_USER
        assert body["sol_password"] == TEST_SOL_PASSWORD_DO_NOT_USE
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_gate_c_authorization_requires_exact_session_requester(session_factory, running_broker, e1b_setup):
    """Gate C evidence: un actor que NO es exactamente `requested_by_user_id`
    de la sesion -- aunque pertenezca al mismo tenant y tenga los permisos
    correctos -- debe ser rechazado
    (`CredentialAuthorizationForbiddenError`), nunca autorizar en su
    nombre. `AUTHORIZATION_REQUIRES_SESSION_REQUESTER=YES`."""
    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)

        other_admin_id = _create_extra_user_same_tenant(
            session_factory, tenant_id=setup["tenant_id"], role="admin"
        )
        wrong_actor = get_user_row(session_factory, other_admin_id)

        try:
            delivery_service.authorize_credential_delivery(
                session_factory,
                grant_registry=running_broker.secret_delivery_grants,
                vault_registry=vault_unlock_registry,
                tenant_id=setup["tenant_id"],
                session_id=setup["session_id"],
                job_id=job_id,
                actor=wrong_actor,
                app_session_id=setup["app_session_id"],
            )
            raise AssertionError("se esperaba CredentialAuthorizationForbiddenError")
        except delivery_service.CredentialAuthorizationForbiddenError:
            pass

        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None
        response = agent.request_credential(job_id)
        assert response.status_code == 409
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])


def test_gate_c_authorization_requires_sol_session_create_and_sol_credential_read(
    session_factory, running_broker, e1b_setup
):
    """Gate C evidence: un actor que SI es el `requested_by_user_id` exacto
    de la sesion, pero cuyo rol (`asistente`) nunca tiene
    `sol_session:create`+`sol_credential:read` (RBAC ya congelado, Domain
    5.1), tambien debe ser rechazado -- la comprobacion del requester por
    si sola nunca es suficiente.
    `AUTHORIZATION_REQUIRES_SOL_SESSION_CREATE=YES`,
    `AUTHORIZATION_REQUIRES_SOL_CREDENTIAL_READ=YES`."""
    from app.services import sol_access_session_service as session_service

    setup = e1b_setup
    try:
        agent = _pair_agent(session_factory, running_broker, setup["tenant_id"], setup["admin_id"])
        job_id = _dispatch_and_claim(session_factory, running_broker, setup, agent)

        # libera el credential (a lo sumo una sesion no-terminal por
        # credencial) para poder crear una nueva sesion con un requester
        # distinto sobre la MISMA credencial real ya cifrada.
        cancel_session = session_factory()
        try:
            session_service.cancel_session(
                cancel_session,
                tenant_id=setup["tenant_id"],
                session_id=setup["session_id"],
                actor_user_id=setup["admin_id"],
            )
            cancel_session.commit()
        finally:
            cancel_session.close()

        asistente_id = _create_extra_user_same_tenant(
            session_factory, tenant_id=setup["tenant_id"], role="asistente"
        )
        asistente_session_id = commit_sol_access_session(
            session_factory,
            tenant_id=setup["tenant_id"],
            client_id=setup["client_id"],
            credential_id=setup["credential_id"],
            requester_id=asistente_id,
        )
        asistente_actor = get_user_row(session_factory, asistente_id)

        try:
            delivery_service.authorize_credential_delivery(
                session_factory,
                grant_registry=running_broker.secret_delivery_grants,
                vault_registry=vault_unlock_registry,
                tenant_id=setup["tenant_id"],
                session_id=asistente_session_id,
                job_id=job_id,
                actor=asistente_actor,
                app_session_id=setup["app_session_id"],
            )
            raise AssertionError("se esperaba CredentialAuthorizationForbiddenError")
        except delivery_service.CredentialAuthorizationForbiddenError:
            pass

        assert running_broker.secret_delivery_grants.peek(tenant_id=setup["tenant_id"], job_id=job_id) is None
    finally:
        cleanup_tenant(session_factory, setup["tenant_id"])
