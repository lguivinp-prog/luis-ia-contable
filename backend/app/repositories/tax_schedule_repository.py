import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tax_schedule import (
    TaxScheduleApprovalHistory,
    TaxScheduleEntry,
    TaxScheduleException,
    TaxScheduleImportRun,
    TaxScheduleSource,
    TaxScheduleVersion,
)

# --- sources ---


def list_sources(db: Session, tenant_id: uuid.UUID) -> list[TaxScheduleSource]:
    return list(
        db.execute(
            select(TaxScheduleSource)
            .where(TaxScheduleSource.tenant_id == tenant_id)
            .order_by(TaxScheduleSource.name)
        )
        .scalars()
        .all()
    )


def get_source(db: Session, tenant_id: uuid.UUID, source_id: uuid.UUID) -> TaxScheduleSource | None:
    return db.execute(
        select(TaxScheduleSource).where(
            TaxScheduleSource.tenant_id == tenant_id, TaxScheduleSource.id == source_id
        )
    ).scalar_one_or_none()


def create_source(db: Session, source: TaxScheduleSource) -> TaxScheduleSource:
    db.add(source)
    db.flush()
    return source


# --- versions ---


def list_versions(
    db: Session, tenant_id: uuid.UUID, *, source_id: uuid.UUID | None = None
) -> list[TaxScheduleVersion]:
    query = select(TaxScheduleVersion).where(TaxScheduleVersion.tenant_id == tenant_id)
    if source_id is not None:
        query = query.where(TaxScheduleVersion.source_id == source_id)
    query = query.order_by(TaxScheduleVersion.created_at.desc())
    return list(db.execute(query).scalars().all())


def get_version(
    db: Session, tenant_id: uuid.UUID, version_id: uuid.UUID
) -> TaxScheduleVersion | None:
    return db.execute(
        select(TaxScheduleVersion).where(
            TaxScheduleVersion.tenant_id == tenant_id, TaxScheduleVersion.id == version_id
        )
    ).scalar_one_or_none()


def create_version(db: Session, version: TaxScheduleVersion) -> TaxScheduleVersion:
    db.add(version)
    db.flush()
    return version


def get_approved_version(
    db: Session,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    fiscal_year: int,
    schedule_type: str,
    scope_category_code: str | None,
) -> TaxScheduleVersion | None:
    return db.execute(
        select(TaxScheduleVersion).where(
            TaxScheduleVersion.tenant_id == tenant_id,
            TaxScheduleVersion.source_id == source_id,
            TaxScheduleVersion.fiscal_year == fiscal_year,
            TaxScheduleVersion.schedule_type == schedule_type,
            TaxScheduleVersion.scope_category_code == scope_category_code,
            TaxScheduleVersion.status == "APPROVED",
        )
    ).scalar_one_or_none()


def list_approved_versions_for_source_year(
    db: Session, tenant_id: uuid.UUID, source_id: uuid.UUID, fiscal_year: int
) -> list[TaxScheduleVersion]:
    """Todas las versiones APPROVED de una fuente+anio, sin filtrar por
    schedule_type/categoria: el resolver decide cual aplica (ver
    tax_schedule_resolver.select_applicable_version, ADR-0015)."""
    return list(
        db.execute(
            select(TaxScheduleVersion).where(
                TaxScheduleVersion.tenant_id == tenant_id,
                TaxScheduleVersion.source_id == source_id,
                TaxScheduleVersion.fiscal_year == fiscal_year,
                TaxScheduleVersion.status == "APPROVED",
            )
        )
        .scalars()
        .all()
    )


# --- entries / exceptions ---


def create_entries(db: Session, entries: list[TaxScheduleEntry]) -> None:
    db.add_all(entries)
    db.flush()


def create_exceptions(db: Session, exceptions: list[TaxScheduleException]) -> None:
    db.add_all(exceptions)
    db.flush()


def list_entries(
    db: Session, tenant_id: uuid.UUID, version_id: uuid.UUID, tax_period: str | None = None
) -> list[TaxScheduleEntry]:
    query = select(TaxScheduleEntry).where(
        TaxScheduleEntry.tenant_id == tenant_id, TaxScheduleEntry.version_id == version_id
    )
    if tax_period is not None:
        query = query.where(TaxScheduleEntry.tax_period == tax_period)
    return list(db.execute(query).scalars().all())


def list_exceptions(
    db: Session, tenant_id: uuid.UUID, version_id: uuid.UUID, tax_period: str | None = None
) -> list[TaxScheduleException]:
    query = select(TaxScheduleException).where(
        TaxScheduleException.tenant_id == tenant_id, TaxScheduleException.version_id == version_id
    )
    if tax_period is not None:
        query = query.where(TaxScheduleException.tax_period == tax_period)
    return list(db.execute(query).scalars().all())


# --- approval history ---


def create_approval_history_entry(
    db: Session, entry: TaxScheduleApprovalHistory
) -> TaxScheduleApprovalHistory:
    db.add(entry)
    db.flush()
    return entry


def list_approval_history(
    db: Session, tenant_id: uuid.UUID, version_id: uuid.UUID
) -> list[TaxScheduleApprovalHistory]:
    return list(
        db.execute(
            select(TaxScheduleApprovalHistory)
            .where(
                TaxScheduleApprovalHistory.tenant_id == tenant_id,
                TaxScheduleApprovalHistory.version_id == version_id,
            )
            .order_by(TaxScheduleApprovalHistory.created_at.asc())
        )
        .scalars()
        .all()
    )


# --- import runs ---


def create_import_run(db: Session, import_run: TaxScheduleImportRun) -> TaxScheduleImportRun:
    db.add(import_run)
    db.flush()
    return import_run


def get_import_run(
    db: Session, tenant_id: uuid.UUID, import_run_id: uuid.UUID
) -> TaxScheduleImportRun | None:
    return db.execute(
        select(TaxScheduleImportRun).where(
            TaxScheduleImportRun.tenant_id == tenant_id, TaxScheduleImportRun.id == import_run_id
        )
    ).scalar_one_or_none()
