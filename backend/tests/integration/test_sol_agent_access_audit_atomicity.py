"""Fase 5, Bloque 5.2, Domain C.1, seccion 10 del encargo: reconfirma que
transicion de estado + registro de auditoria viven en la MISMA transaccion
de SQLAlchemy -- un rollback deshace ambos juntos, nunca deja uno
persistido sin el otro. Se prueba explicitamente tras las correcciones de
Domain C.1 (tiempo autoritativo de BD, rate limit, matriz de expiracion)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.audit_log import AuditAction, AuditLog
from app.models.sol_agent_access import SolAccessSession
from app.services import sol_access_session_service as session_service
from tests.integration._sol_agent_access_helpers import make_client, make_client_sol_credential


def test_session_create_and_audit_roll_back_together(db_session, tenant, admin_user):
    client = make_client(db_session, tenant)
    credential = make_client_sol_credential(db_session, tenant, client, admin_user)

    session_row = session_service.create_session(
        db_session,
        tenant_id=tenant.id,
        client_id=client.id,
        credential_id=credential.id,
        requested_by_user_id=admin_user.id,
        idempotency_key=uuid.uuid4(),
    )
    session_id = session_row.id

    # Antes del rollback: la sesion y su evento de auditoria SOL_SESSION_CREATED
    # son ambos visibles dentro de esta misma transaccion (no comiteada).
    audit_before = db_session.execute(
        select(AuditLog).where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.entity_id == session_id,
            AuditLog.action == AuditAction.SOL_SESSION_CREATED.value,
        )
    ).scalar_one_or_none()
    assert audit_before is not None

    db_session.rollback()

    # Tras el rollback (nunca hubo commit): NINGUNO de los dos quedo
    # persistido -- ni la sesion ni su evento de auditoria por separado.
    session_after = db_session.execute(
        select(SolAccessSession).where(SolAccessSession.id == session_id)
    ).scalar_one_or_none()
    audit_after = db_session.execute(
        select(AuditLog).where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.entity_id == session_id,
            AuditLog.action == AuditAction.SOL_SESSION_CREATED.value,
        )
    ).scalar_one_or_none()
    assert session_after is None
    assert audit_after is None
