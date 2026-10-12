"""M2: API de la Ficha RUC (propuesta + revision humana) y del alta asistida por SUNAT.

Composicion de permisos EXISTENTES (sin permisos nuevos): leer propuestas = `client:read`; aceptar/rechazar = `client:write`;
iniciar un alta asistida exige ademas `sol_credential:create` y `sol_session:create` (el asistente no los tiene). Esta API
NUNCA recibe ni devuelve credenciales SOL: la credencial se guarda por la Boveda existente
(`/clients/{id}/sol-credential`). La Ficha NUNCA se escribe en el cliente sin una aceptacion humana explicita."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.dependencies import verify_csrf
from app.core.permissions import Permission, has_permission, require_permission
from app.db.session import get_db
from app.models.client import Client
from app.models.client_ficha_ruc import ClientFichaRucSnapshot, ClientOnboarding
from app.models.user import User
from app.repositories import client_repository
from app.schemas.ficha_ruc import (
    AcceptFichaRequest,
    CompleteOnboardingRequest,
    DiffEntry,
    FichaOverviewOut,
    FichaSnapshotOut,
    OfficialRegimeOut,
    OnboardingOut,
    RejectFichaRequest,
    StartOnboardingRequest,
)
from app.services import client_onboarding_service as onboarding_service
from app.services import ficha_ruc_service as ficha_service

router = APIRouter(prefix="/api/v1", tags=["ficha-ruc"])

_STATUS_BY_CODE = {
    "FICHA_SNAPSHOT_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "FICHA_CLIENT_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "FICHA_SNAPSHOT_VERSION_CONFLICT": status.HTTP_409_CONFLICT,
    "FICHA_SNAPSHOT_NOT_PENDING": status.HTTP_409_CONFLICT,
    "FICHA_INVALID_FIELD_SELECTION": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "FICHA_INVALID_OVERRIDE": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "ONBOARDING_NOT_FOUND": status.HTTP_404_NOT_FOUND,
    "ONBOARDING_VERSION_CONFLICT": status.HTTP_409_CONFLICT,
    "ONBOARDING_NOT_AWAITING_REVIEW": status.HTTP_409_CONFLICT,
    "ONBOARDING_ALREADY_COMPLETED": status.HTTP_409_CONFLICT,
    "ONBOARDING_CLIENT_ALREADY_EXISTS": status.HTTP_409_CONFLICT,
    "ONBOARDING_RUC_INVALID": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "ONBOARDING_RUC_PREFIX_NOT_SUPPORTED": status.HTTP_422_UNPROCESSABLE_ENTITY,
}


def _error(code: str) -> HTTPException:
    return HTTPException(status_code=_STATUS_BY_CODE.get(code, status.HTTP_409_CONFLICT), detail={"code": code})


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _can_see_sensitive(user: User) -> bool:
    """Representantes legales = datos personales de terceros: solo quien puede EDITAR el cliente (el asistente de solo lectura no)."""
    return has_permission(user, Permission.CLIENT_WRITE)


def _snapshot_out(row: ClientFichaRucSnapshot, user: User) -> FichaSnapshotOut:
    fields, diff = (
        (row.fields, list(row.diff)) if _can_see_sensitive(user) else ficha_service.strip_sensitive(row.fields, list(row.diff))
    )
    return FichaSnapshotOut(
        id=row.id,
        client_id=row.client_id,
        session_id=row.session_id,
        purpose=row.purpose,
        source=row.source,
        schema_version=row.schema_version,
        retrieved_at=row.retrieved_at,
        review_status=row.review_status,
        fields=fields,
        malformed=list(row.malformed or []),
        diff=[DiffEntry(**d) for d in diff],
        accepted_fields=row.accepted_fields,
        reviewed_at=row.reviewed_at,
        version=row.version,
    )


def _onboarding_out(row: ClientOnboarding, client: Client) -> OnboardingOut:
    return OnboardingOut(
        id=row.id,
        client_id=row.client_id,
        ruc=client.ruc,
        status=row.status,
        snapshot_id=row.snapshot_id,
        created_at=row.created_at,
        completed_at=row.completed_at,
        version=row.version,
    )


def _require_client(db: Session, user: User, client_id: uuid.UUID) -> Client:
    client = client_repository.get_by_id(db, user.tenant_id, client_id)
    if client is None:
        raise _error("FICHA_CLIENT_NOT_FOUND")
    return client


@router.get("/clients/{client_id}/ficha-ruc/snapshots", response_model=list[FichaSnapshotOut])
def list_snapshots(
    client_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.CLIENT_READ)),
) -> list[FichaSnapshotOut]:
    _no_store(response)
    _require_client(db, current_user, client_id)
    return [_snapshot_out(r, current_user) for r in ficha_service.list_snapshots(db, tenant_id=current_user.tenant_id, client_id=client_id)]


@router.get("/clients/{client_id}/ficha-ruc/overview", response_model=FichaOverviewOut)
def get_overview(
    client_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.CLIENT_READ)),
) -> FichaOverviewOut:
    """«Información SUNAT» SIN abrir SUNAT: ultimo snapshot ACEPTADO (con su fecha), regimen oficial derivado de sus tributos afectos
    (o `null` si no esta confirmado: nunca un valor por defecto), propuesta pendiente y ultima verificacion sin cambios."""
    _no_store(response)
    client = _require_client(db, current_user, client_id)
    latest = ficha_service.latest_accepted_snapshot(db, tenant_id=current_user.tenant_id, client_id=client_id)
    snapshots = ficha_service.list_snapshots(db, tenant_id=current_user.tenant_id, client_id=client_id, limit=5)
    pending = next((s for s in snapshots if s.review_status == "PENDING_REVIEW"), None)
    regime = ficha_service.snapshot_official_regime(latest)
    regime_source, regime_changed_at = ficha_service.internal_regime_provenance(db, tenant_id=current_user.tenant_id, client_id=client_id)
    return FichaOverviewOut(
        client_id=client_id,
        official_regime=OfficialRegimeOut(
            value=regime,
            label=ficha_service.REGIME_LABELS.get(regime) if regime else None,
            source="SUNAT_FICHA_RUC" if regime else None,
            as_of=latest.retrieved_at if (latest is not None and regime) else None,
            snapshot_id=latest.id if (latest is not None and regime) else None,
        ),
        internal_tax_regime=client.tax_regime,
        internal_regime_source=regime_source,
        internal_regime_changed_at=regime_changed_at,
        latest_accepted=_snapshot_out(latest, current_user) if latest is not None else None,
        pending_snapshot_id=pending.id if pending is not None else None,
        last_verified_at=client.last_verified_at,
        last_unchanged_verification_at=ficha_service.last_unchanged_verification_at(
            db, tenant_id=current_user.tenant_id, client_id=client_id
        ),
        sensitive_sections_visible=_can_see_sensitive(current_user),
    )


@router.get("/clients/{client_id}/ficha-ruc/snapshots/{snapshot_id}", response_model=FichaSnapshotOut)
def get_snapshot(
    client_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.CLIENT_READ)),
) -> FichaSnapshotOut:
    _no_store(response)
    try:
        return _snapshot_out(
            ficha_service.get_snapshot(db, tenant_id=current_user.tenant_id, client_id=client_id, snapshot_id=snapshot_id), current_user
        )
    except ficha_service.FichaServiceError as exc:
        raise _error(exc.code) from None


@router.post("/clients/{client_id}/ficha-ruc/snapshots/{snapshot_id}/accept", response_model=FichaSnapshotOut)
def accept_snapshot(
    client_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    payload: AcceptFichaRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.CLIENT_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> FichaSnapshotOut:
    """Aprobacion HUMANA de campos oficiales concretos (nunca todo por defecto). Un alta asistida se finaliza por
    `/client-onboardings/{id}/complete`, no por aqui."""
    _no_store(response)
    try:
        row = ficha_service.accept_snapshot(
            db, tenant_id=current_user.tenant_id, user=current_user, client_id=client_id, snapshot_id=snapshot_id,
            accepted_fields=payload.accepted_fields, expected_version=payload.expected_version,
        )
        db.commit()
    except ficha_service.FichaServiceError as exc:
        db.rollback()
        raise _error(exc.code) from None
    return _snapshot_out(row, current_user)


@router.post("/clients/{client_id}/ficha-ruc/snapshots/{snapshot_id}/reject", response_model=FichaSnapshotOut)
def reject_snapshot(
    client_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    payload: RejectFichaRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.CLIENT_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> FichaSnapshotOut:
    _no_store(response)
    try:
        row = ficha_service.reject_snapshot(
            db, tenant_id=current_user.tenant_id, user=current_user, client_id=client_id, snapshot_id=snapshot_id,
            expected_version=payload.expected_version,
        )
        db.commit()
    except ficha_service.FichaServiceError as exc:
        db.rollback()
        raise _error(exc.code) from None
    return _snapshot_out(row, current_user)


@router.post("/client-onboardings", response_model=OnboardingOut, status_code=status.HTTP_201_CREATED)
def start_onboarding(
    payload: StartOnboardingRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.CLIENT_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> OnboardingOut:
    """Crea el cliente PROVISIONAL (prospecto) y el alta en curso. No recibe credenciales: el siguiente paso de la UI es
    guardar la credencial por la Boveda y pedir la sesion supervisada con extraccion de Ficha."""
    _no_store(response)
    for needed in (Permission.SOL_CREDENTIAL_CREATE, Permission.SOL_SESSION_CREATE):
        if not has_permission(current_user, needed):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "PERMISSION_DENIED"})
    try:
        onboarding, client = onboarding_service.start_onboarding(
            db, tenant_id=current_user.tenant_id, user=current_user, ruc=payload.ruc
        )
        db.commit()
    except onboarding_service.OnboardingError as exc:
        db.rollback()
        raise _error(exc.code) from None
    return _onboarding_out(onboarding, client)


@router.get("/client-onboardings/{onboarding_id}", response_model=OnboardingOut)
def get_onboarding(
    onboarding_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.CLIENT_WRITE)),
) -> OnboardingOut:
    _no_store(response)
    try:
        row = onboarding_service.get_onboarding(db, tenant_id=current_user.tenant_id, onboarding_id=onboarding_id)
    except onboarding_service.OnboardingError as exc:
        raise _error(exc.code) from None
    return _onboarding_out(row, _require_client(db, current_user, row.client_id))


@router.post("/client-onboardings/{onboarding_id}/complete", response_model=OnboardingOut)
def complete_onboarding(
    onboarding_id: uuid.UUID,
    payload: CompleteOnboardingRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.CLIENT_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> OnboardingOut:
    _no_store(response)
    try:
        row = onboarding_service.complete_onboarding(
            db, tenant_id=current_user.tenant_id, user=current_user, onboarding_id=onboarding_id,
            accepted_fields=payload.accepted_fields, overrides=payload.overrides, activate=payload.activate,
            expected_version=payload.expected_version,
        )
        db.commit()
    except onboarding_service.OnboardingError as exc:
        db.rollback()
        raise _error(exc.code) from None
    return _onboarding_out(row, _require_client(db, current_user, row.client_id))


@router.post("/client-onboardings/{onboarding_id}/cancel", response_model=OnboardingOut)
def cancel_onboarding(
    onboarding_id: uuid.UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission(Permission.CLIENT_WRITE)),
    _csrf: None = Depends(verify_csrf),
) -> OnboardingOut:
    _no_store(response)
    try:
        row = onboarding_service.cancel_onboarding(
            db, tenant_id=current_user.tenant_id, user=current_user, onboarding_id=onboarding_id
        )
        db.commit()
    except onboarding_service.OnboardingError as exc:
        db.rollback()
        raise _error(exc.code) from None
    return _onboarding_out(row, _require_client(db, current_user, row.client_id))
