import uuid

from sqlalchemy.orm import Session

from app.domain.ruc_lookup.provider import RucLookupProvider, RucLookupResult
from app.models.audit_log import AuditAction
from app.models.client import Client
from app.repositories import client_repository
from app.schemas.client import ClientCreate, ClientUpdate
from app.services import audit_service
from app.services.ruc_validator import validate_ruc


class ClientNotFoundError(Exception):
    pass


class DuplicateRucError(Exception):
    pass


_ENUM_FIELDS = {
    "person_type",
    "ruc_status",
    "fiscal_address_condition",
    "tax_regime",
    "issuance_system",
}


def get_client(db: Session, tenant_id: uuid.UUID, client_id: uuid.UUID) -> Client:
    client = client_repository.get_by_id(db, tenant_id, client_id)
    if client is None:
        raise ClientNotFoundError(f"Contribuyente {client_id} no encontrado")
    return client


def list_clients(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    service_status: str | None = None,
    tax_regime: str | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Client], int]:
    return client_repository.list_clients(
        db,
        tenant_id,
        service_status=service_status,
        tax_regime=tax_regime,
        search=search,
        limit=limit,
        offset=offset,
    )


def create_client(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    data: ClientCreate,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> Client:
    validate_ruc(data.ruc)
    if client_repository.get_by_ruc(db, tenant_id, data.ruc) is not None:
        raise DuplicateRucError(f"Ya existe un contribuyente con RUC {data.ruc}")

    client = Client(
        tenant_id=tenant_id,
        created_by=user_id,
        updated_by=user_id,
        **data.model_dump(exclude=_ENUM_FIELDS),
        person_type=data.person_type.value,
        ruc_status=data.ruc_status.value,
        fiscal_address_condition=data.fiscal_address_condition.value,
        tax_regime=data.tax_regime.value,
        issuance_system=data.issuance_system.value if data.issuance_system else None,
    )
    client_repository.create(db, client)

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=AuditAction.CLIENT_CREATE,
        entity_type="client",
        entity_id=client.id,
        metadata={"ruc": client.ruc},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return client


def update_client(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    client_id: uuid.UUID,
    data: ClientUpdate,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> Client:
    client = get_client(db, tenant_id, client_id)
    validate_ruc(data.ruc)

    if data.ruc != client.ruc and client_repository.get_by_ruc(db, tenant_id, data.ruc):
        raise DuplicateRucError(f"Ya existe un contribuyente con RUC {data.ruc}")

    update_values = data.model_dump(exclude=_ENUM_FIELDS)
    for field, value in update_values.items():
        setattr(client, field, value)
    client.person_type = data.person_type.value
    client.ruc_status = data.ruc_status.value
    client.fiscal_address_condition = data.fiscal_address_condition.value
    client.tax_regime = data.tax_regime.value
    client.issuance_system = data.issuance_system.value if data.issuance_system else None
    client.updated_by = user_id

    db.flush()

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=AuditAction.CLIENT_UPDATE,
        entity_type="client",
        entity_id=client.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return client


def change_status(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    client_id: uuid.UUID,
    new_status: str,
    note: str,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> Client:
    client = get_client(db, tenant_id, client_id)
    previous_status = client.service_status
    client.service_status = new_status
    client.updated_by = user_id
    db.flush()

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=AuditAction.CLIENT_STATUS_CHANGE,
        entity_type="client",
        entity_id=client.id,
        metadata={"from": previous_status, "to": new_status, "note": note},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return client


def lookup_ruc(
    db: Session,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    ruc: str,
    provider: RucLookupProvider,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> RucLookupResult:
    validate_ruc(ruc)
    result = provider.lookup(ruc)

    audit_service.record(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        action=AuditAction.RUC_LOOKUP_QUERY,
        entity_type="client",
        metadata={"ruc": ruc, "is_simulated": result.is_simulated},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return result
