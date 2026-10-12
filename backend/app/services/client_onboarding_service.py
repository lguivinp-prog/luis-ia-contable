"""M2: alta asistida por SUNAT. Crea un cliente PROVISIONAL minimo (necesario por las FK de la boveda/credencial; no se
debilita ninguna FK), gobierna su ciclo y lo finaliza SOLO tras la confirmacion humana de la propuesta de la Ficha.

Cliente provisional: `service_status = prospecto` (nunca activo) + campos obligatorios con valores MARCADOR
(`business_name = "Alta asistida pendiente (<ruc>)"`, `fiscal_address_condition = PENDIENTE`) + una fila
`client_onboardings` activa que lo identifica como provisional. Cancelar o fallar NUNCA deja una credencial viva: se
deshabilita la credencial, se cancela la sesion activa y el cliente queda `inactivo` (reintentable con el mismo RUC:
se reutiliza la fila). La credencial SOL solo se almacena por la Boveda existente; este servicio nunca la ve."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.models.audit_log import AuditAction
from app.models.client import Client
from app.models.client_ficha_ruc import ClientFichaRucSnapshot, ClientOnboarding, OnboardingStatus
from app.models.user import User
from app.repositories import sol_agent_access_repository as repo
from app.repositories import sol_vault_repository as vault_repo
from app.services import audit_service, ficha_ruc_service
from app.services import client_sol_credential_service as credential_service
from app.services import sol_access_session_service as session_service
from app.services.ruc_validator import is_valid_ruc


class OnboardingError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _get_for_update(db: Session, tenant_id: uuid.UUID, onboarding_id: uuid.UUID) -> ClientOnboarding:
    row = db.execute(
        select(ClientOnboarding)
        .where(ClientOnboarding.tenant_id == tenant_id, ClientOnboarding.id == onboarding_id)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise OnboardingError("ONBOARDING_NOT_FOUND")
    return row


def get_onboarding(db: Session, *, tenant_id: uuid.UUID, onboarding_id: uuid.UUID) -> ClientOnboarding:
    row = db.execute(
        select(ClientOnboarding).where(ClientOnboarding.tenant_id == tenant_id, ClientOnboarding.id == onboarding_id)
    ).scalar_one_or_none()
    if row is None:
        raise OnboardingError("ONBOARDING_NOT_FOUND")
    return row


def start_onboarding(db: Session, *, tenant_id: uuid.UUID, user: User, ruc: str) -> tuple[ClientOnboarding, Client]:
    if not is_valid_ruc(ruc):
        raise OnboardingError("ONBOARDING_RUC_INVALID")
    person_type = ficha_ruc_service.person_type_from_ruc(ruc)
    if person_type is None:
        raise OnboardingError("ONBOARDING_RUC_PREFIX_NOT_SUPPORTED")

    client = db.execute(
        select(Client).where(Client.tenant_id == tenant_id, Client.ruc == ruc).with_for_update()
    ).scalar_one_or_none()
    if client is None:
        client = Client(
            tenant_id=tenant_id,
            created_by=user.id,
            updated_by=user.id,
            ruc=ruc,
            business_name=f"Alta asistida pendiente ({ruc})",
            person_type=person_type,
            ruc_status="ACTIVO",
            fiscal_address_condition="PENDIENTE",
            tax_regime="SIN_DETERMINAR",  # nunca un regimen por defecto: se propone desde los tributos afectos de la Ficha
            service_status="prospecto",
        )
        db.add(client)
        db.flush()
    else:
        previous = db.execute(
            select(ClientOnboarding)
            .where(ClientOnboarding.tenant_id == tenant_id, ClientOnboarding.client_id == client.id)
            .order_by(ClientOnboarding.created_at.desc())
        ).scalars().all()
        active = [o for o in previous if o.status in {"IN_PROGRESS", "AWAITING_REVIEW"}]
        if active:
            return active[0], client  # idempotente: ya hay un alta en curso para este RUC
        # Solo se reutiliza un cliente provisional que NUNCA fue finalizado (alta cancelada/fallida).
        if not previous or any(o.status == "COMPLETED" for o in previous) or client.service_status != "inactivo":
            raise OnboardingError("ONBOARDING_CLIENT_ALREADY_EXISTS")
        client.service_status = "prospecto"
        client.updated_by = user.id
    onboarding = ClientOnboarding(
        tenant_id=tenant_id, client_id=client.id, created_by=user.id, status=OnboardingStatus.IN_PROGRESS.value
    )
    db.add(onboarding)
    db.flush()
    audit_service.record(
        db, tenant_id=tenant_id, user_id=user.id, action=AuditAction.ONBOARDING_STARTED,
        entity_type="client_onboarding", entity_id=onboarding.id, metadata={"client_id": str(client.id)},
    )
    return onboarding, client


def complete_onboarding(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user: User,
    onboarding_id: uuid.UUID,
    accepted_fields: list[str],
    overrides: dict[str, str],
    activate: bool,
    expected_version: int,
) -> ClientOnboarding:
    """Confirmacion humana unica: aplica los campos oficiales elegidos y finaliza el cliente. Sin revision aceptada,
    el cliente jamas se activa."""
    onboarding = _get_for_update(db, tenant_id, onboarding_id)
    if onboarding.version != expected_version:
        raise OnboardingError("ONBOARDING_VERSION_CONFLICT")
    if onboarding.status != OnboardingStatus.AWAITING_REVIEW.value or onboarding.snapshot_id is None:
        raise OnboardingError("ONBOARDING_NOT_AWAITING_REVIEW")
    snapshot = db.get(ClientFichaRucSnapshot, onboarding.snapshot_id)
    if snapshot is None or snapshot.tenant_id != tenant_id:
        raise OnboardingError("ONBOARDING_NOT_AWAITING_REVIEW")
    try:
        ficha_ruc_service.accept_snapshot(
            db, tenant_id=tenant_id, user=user, client_id=onboarding.client_id, snapshot_id=snapshot.id,
            accepted_fields=accepted_fields, expected_version=snapshot.version, overrides=overrides,
        )
    except ficha_ruc_service.FichaServiceError as exc:
        raise OnboardingError(exc.code) from None
    client = db.execute(
        select(Client).where(Client.tenant_id == tenant_id, Client.id == onboarding.client_id).with_for_update()
    ).scalar_one()
    client.service_status = "activo" if activate else "prospecto"
    client.updated_by = user.id
    onboarding.status = OnboardingStatus.COMPLETED.value
    onboarding.completed_at = clock.db_now(db)
    onboarding.version += 1
    db.flush()
    audit_service.record(
        db, tenant_id=tenant_id, user_id=user.id, action=AuditAction.ONBOARDING_COMPLETED,
        entity_type="client_onboarding", entity_id=onboarding.id,
        metadata={"client_id": str(client.id), "activated": bool(activate)},
    )
    return onboarding


def cancel_onboarding(
    db: Session, *, tenant_id: uuid.UUID, user: User, onboarding_id: uuid.UUID
) -> ClientOnboarding:
    """Cancela un alta no finalizada: sesion activa cancelada, credencial DESHABILITADA (nunca queda una credencial
    viva huerfana), propuesta pendiente rechazada y cliente provisional `inactivo`. Idempotente."""
    onboarding = _get_for_update(db, tenant_id, onboarding_id)
    if onboarding.status == OnboardingStatus.CANCELLED.value:
        return onboarding
    if onboarding.status not in {"IN_PROGRESS", "AWAITING_REVIEW", "FAILED"}:
        raise OnboardingError("ONBOARDING_ALREADY_COMPLETED")

    session = repo.get_latest_session_for_client(db, tenant_id=tenant_id, client_id=onboarding.client_id)
    if session is not None and session.status not in {"SUCCEEDED", "PARTIAL_FAILURE", "FAILED", "CANCELLED", "EXPIRED"}:
        session_service.cancel_session(db, tenant_id=tenant_id, session_id=session.id, actor_user_id=user.id)
    credential = vault_repo.get_active_credential_for_client(db, tenant_id=tenant_id, client_id=onboarding.client_id)
    if credential is not None and credential.status != "DISABLED":
        credential_service.disable_credential(
            db, tenant_id=tenant_id, client_id=onboarding.client_id, credential_id=credential.id, actor=user,
            expected_version=credential.version,
        )
    if onboarding.snapshot_id is not None:
        snapshot = db.get(ClientFichaRucSnapshot, onboarding.snapshot_id)
        if snapshot is not None and snapshot.review_status == "PENDING_REVIEW":
            snapshot.review_status = "REJECTED"
            snapshot.reviewed_by = user.id
            snapshot.reviewed_at = clock.db_now(db)
            snapshot.version += 1
    client = db.execute(
        select(Client).where(Client.tenant_id == tenant_id, Client.id == onboarding.client_id).with_for_update()
    ).scalar_one()
    client.service_status = "inactivo"
    client.updated_by = user.id
    onboarding.status = OnboardingStatus.CANCELLED.value
    onboarding.version += 1
    db.flush()
    audit_service.record(
        db, tenant_id=tenant_id, user_id=user.id, action=AuditAction.ONBOARDING_CANCELLED,
        entity_type="client_onboarding", entity_id=onboarding.id, metadata={"client_id": str(client.id)},
    )
    return onboarding
