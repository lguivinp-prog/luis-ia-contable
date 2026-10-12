"""M2: snapshots estructurados de la Ficha RUC, propuesta/diff y revision humana ANTES de escribir.

Frontera de autoridad: el Agente reporta campos estructurados (ya parseados con una lista blanca de rotulos); este
servicio los VALIDA estrictamente (`FichaRucFieldsV1`), comprueba que el RUC coincide con el del cliente
(`WRONG_TAXPAYER_CONTEXT` => la sesion FALLA), calcula el DIFF contra el cliente y deja el snapshot `PENDING_REVIEW`.
NADA se escribe en el cliente hasta que un humano acepta campos concretos (`accept_snapshot`). Los metadatos internos del
negocio (telefono, correo, WhatsApp, honorario, etiquetas, notas, estado de servicio, responsables) NUNCA se tocan.

Se persiste solo el resultado estructurado: jamas HTML crudo, DOM, cookies ni capturas. La auditoria registra nombres de
campos, nunca valores ni credenciales."""

from __future__ import annotations

import unicodedata
import re
import uuid
from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import clock
from app.models.audit_log import AuditAction, AuditLog
from app.models.client import Client
from app.models.client_ficha_ruc import (
    FICHA_SOURCE,
    ClientFichaRucSnapshot,
    ClientOnboarding,
    FichaPurpose,
    FichaReviewStatus,
    OnboardingStatus,
)
from app.models.user import User
from app.repositories import sol_agent_access_repository as repo
from app.schemas.ficha_ruc import FICHA_SCHEMA_VERSION, SENSITIVE_SECTIONS, FichaRucFieldsV1
from app.services import audit_service
from app.services import sol_access_session_service as session_service
from app.services import sol_agent_job_service as job_service

# campo oficial del cliente -> rotulo para la pantalla de revision
OFFICIAL_FIELD_LABELS: dict[str, str] = {
    "person_type": "Tipo de persona",
    "business_name": "Razón social / nombres",
    "trade_name": "Nombre comercial",
    "taxpayer_type": "Tipo de contribuyente",
    "ruc_status": "Estado del contribuyente",
    "fiscal_address_condition": "Condición del domicilio fiscal",
    "fiscal_address": "Domicilio fiscal",
    "activities_start_date": "Inicio de actividades",
    "main_economic_activity": "Actividad económica principal",
    "secondary_economic_activities": "Actividades económicas secundarias",
    "department": "Departamento (domicilio fiscal)",
    "province": "Provincia (domicilio fiscal)",
    "district": "Distrito (domicilio fiscal)",
    "tax_regime": "Régimen del impuesto a la renta (según los tributos afectos)",
}
# Secciones de la Ficha que NO son columnas del cliente: se comparan contra el ultimo snapshot ACEPTADO y su aceptacion solo deja constancia
# (no escribe nada en el cliente). Se muestran en «Información SUNAT».
SNAPSHOT_SECTION_LABELS: dict[str, str] = {
    "dependencia_sunat": "Dependencia SUNAT",
    "tributos_afectos": "Registro de tributos afectos",
    "establecimientos_anexos": "Establecimientos anexos",
    "representantes_legales": "Representantes legales",
}
SNAPSHOT_SECTIONS = tuple(SNAPSHOT_SECTION_LABELS)
# Mapa EXACTO (tras normalizar) del tributo de renta al regimen. Solo lo OBSERVADO en la Ficha real; cualquier otro texto => sin regimen
# (jamas se infiere ni se usa un valor por defecto).
_REGIME_BY_RENTA_TRIBUTE = {"RENTA - REGIMEN MYPE TRIBUTARIO": "RMT"}
REGIME_LABELS = {
    "RMT": "Régimen MYPE Tributario (RMT)",
    "GENERAL": "Régimen General",
    "RER": "Régimen Especial de Renta (RER)",
    "NRUS": "Nuevo RUS (NRUS)",
    "SIN_DETERMINAR": "Sin determinar",
}
# correcciones humanas permitidas (solo textos oficiales; nunca estados ni fechas)
OVERRIDABLE_FIELDS = frozenset({"business_name", "trade_name", "fiscal_address", "taxpayer_type", "main_economic_activity"})
_OVERRIDE_MAX = {"business_name": 255, "trade_name": 255, "fiscal_address": 500, "taxpayer_type": 100, "main_economic_activity": 255}

_RUC_STATUS_MAP = {
    "ACTIVO": "ACTIVO",
    "BAJA PROVISIONAL": "BAJA_PROVISIONAL",
    "BAJA DEFINITIVA": "BAJA_DEFINITIVA",
    "BAJA DE OFICIO": "BAJA_DE_OFICIO",
    "SUSPENSION TEMPORAL": "SUSPENSION_TEMPORAL",
}
_CONDITION_MAP = {"HABIDO": "HABIDO", "NO HABIDO": "NO_HABIDO", "NO HALLADO": "NO_HALLADO", "PENDIENTE": "PENDIENTE"}


def _fold(text: str | None) -> str:
    stripped = unicodedata.normalize("NFD", text or "")
    return re.sub(r"\s+", " ", "".join(c for c in stripped if unicodedata.category(c) != "Mn")).strip().upper()


def map_ruc_status(raw: str | None) -> str | None:
    """Solo coincidencias EXACTAS tras normalizar; cualquier otro texto => None (no se infiere)."""
    return _RUC_STATUS_MAP.get(_fold(raw))


def map_address_condition(raw: str | None) -> str | None:
    return _CONDITION_MAP.get(_fold(raw))


def official_regime_from_tributos(tributos: list[dict] | None) -> str | None:
    """Regimen del impuesto a la renta SOLO si los tributos afectos traen UNA fila de renta con un texto conocido. Ambiguo (varias filas
    de renta distintas), desconocido o ausente => None. Presentar un tributo NO asigna obligaciones mensuales."""
    mapped: set[str] = set()
    for row in tributos or []:
        name = _fold(str(row.get("tributo", "")))
        if not name.startswith("RENTA"):
            continue
        regime = _REGIME_BY_RENTA_TRIBUTE.get(name)
        if regime is None:
            return None  # una fila de renta que no se reconoce: no se infiere el regimen
        mapped.add(regime)
    return mapped.pop() if len(mapped) == 1 else None


def compose_fiscal_address(domicilio) -> str | None:
    """Direccion de la Ficha armada SOLO con partes presentes (via, numero, interior...; zona; referencias). Departamento/provincia/
    distrito van a sus propias columnas."""
    if domicilio is None:
        return None
    d = domicilio
    street = " ".join(x for x in (d.via, d.numero) if x)
    detail = " ".join(f"{label} {value}" for label, value in (("KM", d.km), ("MZ", d.manzana), ("LOTE", d.lote), ("DPTO", d.dpto), ("INT", d.interior)) if value)
    parts = [p for p in (street, detail, d.zona, d.referencias) if p]
    return ", ".join(parts)[:500] or None


def person_type_from_ruc(ruc: str) -> str | None:
    """Regla determinista por prefijo: 10 => natural, 20 => juridica; otros prefijos NO se infieren."""
    return {"10": "natural", "20": "juridica"}.get(ruc[:2])


class FichaServiceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class StoreOutcome:
    accepted: bool
    idempotent: bool = False
    wrong_taxpayer: bool = False
    snapshot_id: uuid.UUID | None = None
    unchanged: bool = False  # Flujo B: Ficha verificada sin cambios (no se creo ninguna propuesta)


def proposal_values(fields: FichaRucFieldsV1) -> dict[str, object]:
    """Valores PROPUESTOS por la Ficha para los campos oficiales del cliente (solo los realmente presentes)."""
    out: dict[str, object] = {}
    pt = person_type_from_ruc(fields.ruc)
    if pt:
        out["person_type"] = pt
    if fields.razon_social:
        out["business_name"] = fields.razon_social
    if fields.nombre_comercial:
        out["trade_name"] = fields.nombre_comercial
    if fields.tipo_contribuyente:
        out["taxpayer_type"] = fields.tipo_contribuyente
    status = map_ruc_status(fields.estado_contribuyente)
    if status:
        out["ruc_status"] = status
    condition = map_address_condition(fields.condicion_contribuyente)
    if condition:
        out["fiscal_address_condition"] = condition
    composed = compose_fiscal_address(fields.domicilio)
    if composed:
        out["fiscal_address"] = composed
    elif fields.domicilio_fiscal:
        out["fiscal_address"] = fields.domicilio_fiscal
    if fields.domicilio is not None:
        for column, value in (("department", fields.domicilio.departamento), ("province", fields.domicilio.provincia), ("district", fields.domicilio.distrito)):
            if value:
                out[column] = value[:60]
    regime = official_regime_from_tributos([t.model_dump(mode="json") for t in fields.tributos_afectos])
    if regime:
        out["tax_regime"] = regime
    if fields.fecha_inicio_actividades:
        out["activities_start_date"] = fields.fecha_inicio_actividades.isoformat()
    main = [a for a in fields.actividades_economicas if a.tipo == "PRINCIPAL"]
    if main:
        a = main[0]
        out["main_economic_activity"] = f"{a.ciiu} - {a.descripcion}"[:255] if a.ciiu else a.descripcion[:255]
    secondary = [a for a in fields.actividades_economicas if a.tipo == "SECUNDARIA"]
    if secondary:
        # El esquema del cliente guarda las actividades secundarias como lista de TEXTOS ("CIIU - descripcion").
        out["secondary_economic_activities"] = [
            (f"{a.ciiu} - {a.descripcion}" if a.ciiu else a.descripcion)[:255] for a in secondary
        ]
    return out


def current_values(client: Client) -> dict[str, object]:
    return {
        "person_type": client.person_type,
        "business_name": client.business_name,
        "trade_name": client.trade_name,
        "taxpayer_type": client.taxpayer_type,
        "ruc_status": client.ruc_status,
        "fiscal_address_condition": client.fiscal_address_condition,
        "fiscal_address": client.fiscal_address,
        "activities_start_date": client.activities_start_date.isoformat() if client.activities_start_date else None,
        "main_economic_activity": client.main_economic_activity,
        "secondary_economic_activities": client.secondary_economic_activities,
        "department": client.department,
        "province": client.province,
        "district": client.district,
        "tax_regime": client.tax_regime,
    }


def latest_accepted_snapshot(db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID) -> ClientFichaRucSnapshot | None:
    return db.execute(
        select(ClientFichaRucSnapshot)
        .where(
            ClientFichaRucSnapshot.tenant_id == tenant_id,
            ClientFichaRucSnapshot.client_id == client_id,
            ClientFichaRucSnapshot.review_status == FichaReviewStatus.ACCEPTED.value,
        )
        .order_by(ClientFichaRucSnapshot.retrieved_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def build_diff(client: Client, fields: FichaRucFieldsV1, previous_fields: dict | None = None) -> list[dict]:
    """Diff por campo: columnas oficiales del cliente (contra el cliente) + secciones de la Ficha (contra el ultimo snapshot
    ACEPTADO; sin snapshot previo todo lo presente es nuevo). Un campo ausente en la Ficha NO genera diferencia (no se borra nada)."""
    current = current_values(client)
    diff = []
    for key, proposed in proposal_values(fields).items():
        diff.append(
            {
                "field": key,
                "label": OFFICIAL_FIELD_LABELS[key],
                "current": current.get(key),
                "proposed": proposed,
                "changed": current.get(key) != proposed,
            }
        )
    dumped = fields.model_dump(mode="json", exclude_none=True)
    previous = previous_fields or {}
    for key in SNAPSHOT_SECTIONS:
        proposed = dumped.get(key)
        if proposed in (None, [], {}):
            continue
        before = previous.get(key)
        diff.append(
            {"field": key, "label": SNAPSHOT_SECTION_LABELS[key], "current": before if before not in ([], {}) else None,
             "proposed": proposed, "changed": before != proposed}
        )
    return diff


def strip_sensitive(fields: dict, diff: list[dict]) -> tuple[dict, list[dict]]:
    """Quita los datos personales de terceros (representantes legales) para quien NO puede editar el cliente."""
    safe_fields = {k: v for k, v in fields.items() if k not in SENSITIVE_SECTIONS}
    safe_diff = [d for d in diff if d.get("field") not in SENSITIVE_SECTIONS]
    return safe_fields, safe_diff


def snapshot_official_regime(snapshot: ClientFichaRucSnapshot | None) -> str | None:
    return official_regime_from_tributos((snapshot.fields or {}).get("tributos_afectos")) if snapshot is not None else None


def internal_regime_provenance(db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID) -> tuple[str, object]:
    """Quien fijo por ULTIMA vez el regimen interno del cliente: `MANUAL` (correccion de una persona), `SUNAT_FICHA_RUC` (aceptacion humana de
    una propuesta de la Ficha) o `NO_REGISTRADO` (valor inicial/por defecto, sin evidencia). Se deduce de la auditoria, nunca se supone."""
    manual = db.execute(
        select(AuditLog.created_at)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.action == AuditAction.CLIENT_TAX_REGIME_MANUAL_CHANGE.value,
               AuditLog.entity_type == "client", AuditLog.entity_id == client_id)
        .order_by(AuditLog.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    accepted = db.execute(
        select(AuditLog.created_at)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.action == AuditAction.FICHA_PROPOSAL_ACCEPTED.value,
               AuditLog.log_metadata["client_id"].astext == str(client_id), AuditLog.log_metadata["accepted_fields"].contains(["tax_regime"]))
        .order_by(AuditLog.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if manual is not None and (accepted is None or manual >= accepted):
        return "MANUAL", manual
    if accepted is not None:
        return "SUNAT_FICHA_RUC", accepted
    return "NO_REGISTRADO", None


def last_unchanged_verification_at(db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID):
    return db.execute(
        select(AuditLog.created_at)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.action == AuditAction.FICHA_VERIFIED_NO_CHANGES.value,
            AuditLog.entity_type == "client",
            AuditLog.entity_id == client_id,
        )
        .order_by(AuditLog.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_snapshot(db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID, snapshot_id: uuid.UUID) -> ClientFichaRucSnapshot:
    row = db.execute(
        select(ClientFichaRucSnapshot).where(
            ClientFichaRucSnapshot.tenant_id == tenant_id,
            ClientFichaRucSnapshot.client_id == client_id,
            ClientFichaRucSnapshot.id == snapshot_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise FichaServiceError("FICHA_SNAPSHOT_NOT_FOUND")
    return row


def list_snapshots(db: Session, *, tenant_id: uuid.UUID, client_id: uuid.UUID, limit: int = 20) -> list[ClientFichaRucSnapshot]:
    return list(
        db.execute(
            select(ClientFichaRucSnapshot)
            .where(ClientFichaRucSnapshot.tenant_id == tenant_id, ClientFichaRucSnapshot.client_id == client_id)
            .order_by(ClientFichaRucSnapshot.retrieved_at.desc())
            .limit(limit)
        ).scalars()
    )


def store_snapshot_from_agent(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    job_id: uuid.UUID,
    claimed_session_id: uuid.UUID | None,
    schema_version: int,
    raw_fields: dict,
    malformed: list[str],
) -> StoreOutcome:
    """Valida y persiste el snapshot del Agente. El llamador (broker) hace commit. Idempotente por sesion."""
    try:
        session_row, job = job_service._lock_session_then_job(db, tenant_id=tenant_id, job_id=job_id)
    except job_service.JobNotFoundError:
        raise FichaServiceError("FICHA_JOB_NOT_FOUND") from None
    if job.agent_id != agent_id or (claimed_session_id is not None and claimed_session_id != job.session_id):
        raise FichaServiceError("FICHA_JOB_NOT_FOUND")
    if not session_row.extract_ficha or session_row.destination != "FICHA_RUC":
        raise FichaServiceError("FICHA_EXTRACTION_NOT_REQUESTED")
    if schema_version != FICHA_SCHEMA_VERSION:
        raise FichaServiceError("FICHA_SCHEMA_UNSUPPORTED")

    try:
        fields = FichaRucFieldsV1.model_validate(raw_fields)
    except ValidationError:
        raise FichaServiceError("FICHA_FIELDS_INVALID") from None
    clean_malformed = sorted({m for m in malformed if re.fullmatch(r"[a-z_]{1,40}", m)})[:20]

    existing = db.execute(
        select(ClientFichaRucSnapshot).where(ClientFichaRucSnapshot.session_id == session_row.id)
    ).scalar_one_or_none()
    if existing is not None:
        if existing.fields == fields.model_dump(mode="json", exclude_none=True, exclude_defaults=False):
            return StoreOutcome(True, idempotent=True, snapshot_id=existing.id)
        raise FichaServiceError("FICHA_SNAPSHOT_CONFLICT")

    if job.status != "CLAIMED" or job.credential_delivered_at is None or session_row.status not in {"IN_PROGRESS", "WAITING_FOR_USER"}:
        raise FichaServiceError("FICHA_SESSION_STATE_INVALID")
    if session_row.status == "WAITING_FOR_USER":
        # el humano resolvio un aviso de SUNAT y la automatizacion retomo la navegacion hasta la Ficha (transicion legal)
        session_row = session_service.start_progress(
            db, tenant_id=tenant_id, session_id=session_row.id, expected_version=session_row.version
        )

    client = db.execute(
        select(Client).where(Client.tenant_id == tenant_id, Client.id == session_row.client_id)
    ).scalar_one_or_none()
    if client is None:
        raise FichaServiceError("FICHA_CLIENT_NOT_FOUND")

    if fields.ruc != client.ruc:
        # Contribuyente equivocado: regla dura de la maquina de estados (FAILED + WRONG_TAXPAYER_CONTEXT).
        session_service.report_failed(
            db, tenant_id=tenant_id, session_id=session_row.id, expected_version=session_row.version,
            terminal_reason_code="WRONG_TAXPAYER_CONTEXT",
        )
        job_service.fail_job(db, tenant_id=tenant_id, job_id=job.id, expected_version=job.version)
        return StoreOutcome(False, wrong_taxpayer=True)

    onboarding = db.execute(
        select(ClientOnboarding).where(
            ClientOnboarding.tenant_id == tenant_id,
            ClientOnboarding.client_id == client.id,
            ClientOnboarding.status == OnboardingStatus.IN_PROGRESS.value,
        )
    ).scalar_one_or_none()
    purpose = FichaPurpose.ONBOARDING.value if onboarding is not None else FichaPurpose.REFRESH.value

    # la propuesta nueva supera a una pendiente anterior del mismo cliente (a lo sumo UNA pendiente)
    db.execute(
        update(ClientFichaRucSnapshot)
        .where(
            ClientFichaRucSnapshot.tenant_id == tenant_id,
            ClientFichaRucSnapshot.client_id == client.id,
            ClientFichaRucSnapshot.review_status == FichaReviewStatus.PENDING_REVIEW.value,
        )
        .values(review_status=FichaReviewStatus.SUPERSEDED.value)
    )
    stored = fields.model_dump(mode="json", exclude_none=True)
    previous = latest_accepted_snapshot(db, tenant_id=tenant_id, client_id=client.id)
    diff = build_diff(client, fields, previous.fields if previous is not None else None)
    if onboarding is None and previous is not None and diff and not any(d["changed"] for d in diff):
        # Flujo B: la Ficha es IDENTICA al ultimo snapshot aceptado y el cliente ya coincide: no se crea otro snapshot ni se fabrica una
        # diferencia. Solo queda constancia de la verificacion (fecha de consulta).
        client.last_verified_at = clock.db_now(db)
        audit_service.record(
            db, tenant_id=tenant_id, user_id=None, action=AuditAction.FICHA_VERIFIED_NO_CHANGES,
            entity_type="client", entity_id=client.id,
            metadata={"session_id": str(session_row.id), "snapshot_id": str(previous.id)},
        )
        return StoreOutcome(True, idempotent=True, snapshot_id=previous.id, unchanged=True)
    snapshot = ClientFichaRucSnapshot(
        tenant_id=tenant_id,
        client_id=client.id,
        session_id=session_row.id,
        purpose=purpose,
        source=FICHA_SOURCE,
        schema_version=schema_version,
        retrieved_at=clock.db_now(db),
        fields=stored,
        malformed=clean_malformed,
        diff=diff,
        review_status=FichaReviewStatus.PENDING_REVIEW.value,
    )
    db.add(snapshot)
    db.flush()
    if onboarding is not None:
        onboarding.snapshot_id = snapshot.id
        onboarding.status = OnboardingStatus.AWAITING_REVIEW.value
        onboarding.version += 1
        db.flush()
    changed = [d["field"] for d in snapshot.diff if d["changed"]]
    audit_service.record(
        db, tenant_id=tenant_id, user_id=None, action=AuditAction.FICHA_RUC_RETRIEVED,
        entity_type="sol_access_session", entity_id=session_row.id,
        metadata={"snapshot_id": str(snapshot.id), "schema_version": schema_version, "fields_count": len(stored)},
    )
    audit_service.record(
        db, tenant_id=tenant_id, user_id=None, action=AuditAction.FICHA_PROPOSAL_CREATED,
        entity_type="client_ficha_ruc_snapshot", entity_id=snapshot.id,
        metadata={"client_id": str(client.id), "purpose": purpose, "changed_fields": changed},
    )
    return StoreOutcome(True, snapshot_id=snapshot.id)


def _apply_to_client(client: Client, key: str, value: object) -> None:
    if key in SNAPSHOT_SECTIONS:
        return  # secciones de la Ficha: la aceptacion deja constancia en el snapshot; no hay columna del cliente que escribir
    if key == "activities_start_date":
        client.activities_start_date = date.fromisoformat(str(value)) if value else None
    elif key == "secondary_economic_activities":
        client.secondary_economic_activities = value  # type: ignore[assignment]
    else:
        setattr(client, key, value)


def accept_snapshot(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    user: User,
    client_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    accepted_fields: list[str],
    expected_version: int,
    overrides: dict[str, str] | None = None,
) -> ClientFichaRucSnapshot:
    """El HUMANO aprueba campos concretos. Solo se aplican campos oficiales con cambio; nada interno se toca."""
    snapshot = db.execute(
        select(ClientFichaRucSnapshot)
        .where(
            ClientFichaRucSnapshot.tenant_id == tenant_id,
            ClientFichaRucSnapshot.client_id == client_id,
            ClientFichaRucSnapshot.id == snapshot_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if snapshot is None:
        raise FichaServiceError("FICHA_SNAPSHOT_NOT_FOUND")
    if snapshot.version != expected_version:
        raise FichaServiceError("FICHA_SNAPSHOT_VERSION_CONFLICT")
    if snapshot.review_status != FichaReviewStatus.PENDING_REVIEW.value:
        raise FichaServiceError("FICHA_SNAPSHOT_NOT_PENDING")
    client = db.execute(
        select(Client).where(Client.tenant_id == tenant_id, Client.id == client_id).with_for_update()
    ).scalar_one_or_none()
    if client is None:
        raise FichaServiceError("FICHA_CLIENT_NOT_FOUND")

    entries = {d["field"]: d for d in snapshot.diff}
    selected = list(dict.fromkeys(accepted_fields))
    if any(k not in entries or not entries[k]["changed"] for k in selected):
        raise FichaServiceError("FICHA_INVALID_FIELD_SELECTION")
    overrides = overrides or {}
    for key, value in overrides.items():
        if key not in OVERRIDABLE_FIELDS or key not in selected or len(value) > _OVERRIDE_MAX[key]:
            raise FichaServiceError("FICHA_INVALID_OVERRIDE")

    for key in selected:
        _apply_to_client(client, key, overrides.get(key, entries[key]["proposed"]))
    client.last_verified_at = clock.db_now(db)
    client.updated_by = user.id
    now = clock.db_now(db)
    snapshot.review_status = FichaReviewStatus.ACCEPTED.value
    snapshot.accepted_fields = selected
    snapshot.reviewed_by = user.id
    snapshot.reviewed_at = now
    snapshot.version += 1
    db.flush()
    audit_service.record(
        db, tenant_id=tenant_id, user_id=user.id, action=AuditAction.FICHA_PROPOSAL_ACCEPTED,
        entity_type="client_ficha_ruc_snapshot", entity_id=snapshot.id,
        metadata={"client_id": str(client_id), "accepted_fields": selected, "overridden_fields": sorted(overrides)},
    )
    return snapshot


def reject_snapshot(
    db: Session, *, tenant_id: uuid.UUID, user: User, client_id: uuid.UUID, snapshot_id: uuid.UUID, expected_version: int
) -> ClientFichaRucSnapshot:
    snapshot = db.execute(
        select(ClientFichaRucSnapshot)
        .where(
            ClientFichaRucSnapshot.tenant_id == tenant_id,
            ClientFichaRucSnapshot.client_id == client_id,
            ClientFichaRucSnapshot.id == snapshot_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if snapshot is None:
        raise FichaServiceError("FICHA_SNAPSHOT_NOT_FOUND")
    if snapshot.version != expected_version:
        raise FichaServiceError("FICHA_SNAPSHOT_VERSION_CONFLICT")
    if snapshot.review_status != FichaReviewStatus.PENDING_REVIEW.value:
        raise FichaServiceError("FICHA_SNAPSHOT_NOT_PENDING")
    snapshot.review_status = FichaReviewStatus.REJECTED.value
    snapshot.reviewed_by = user.id
    snapshot.reviewed_at = clock.db_now(db)
    snapshot.version += 1
    # un alta asistida cuya propuesta se rechaza vuelve a "en curso": se puede reintentar o cancelar
    onboarding = db.execute(
        select(ClientOnboarding)
        .where(
            ClientOnboarding.tenant_id == tenant_id,
            ClientOnboarding.client_id == client_id,
            ClientOnboarding.snapshot_id == snapshot.id,
            ClientOnboarding.status == OnboardingStatus.AWAITING_REVIEW.value,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if onboarding is not None:
        onboarding.status = OnboardingStatus.IN_PROGRESS.value
        onboarding.snapshot_id = None
        onboarding.version += 1
    db.flush()
    audit_service.record(
        db, tenant_id=tenant_id, user_id=user.id, action=AuditAction.FICHA_PROPOSAL_REJECTED,
        entity_type="client_ficha_ruc_snapshot", entity_id=snapshot.id, metadata={"client_id": str(client_id)},
    )
    return snapshot
