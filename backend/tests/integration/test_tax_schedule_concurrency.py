import pytest
from sqlalchemy.exc import IntegrityError

from app.models.tax_schedule import TaxScheduleSource, TaxScheduleVersion
from app.repositories import tax_schedule_repository
from app.services import tax_schedule_service
from app.services.tax_schedule_service import ConcurrentApprovalConflictError


def _make_source(db_session, tenant_id, obligation_id) -> TaxScheduleSource:
    source = TaxScheduleSource(
        tenant_id=tenant_id, obligation_id=obligation_id, name="Cronograma concurrencia", is_active=True
    )
    db_session.add(source)
    db_session.flush()
    return source


def _make_version(
    db_session, tenant_id, source_id, *, status: str, fiscal_year: int = 2026
) -> TaxScheduleVersion:
    version = TaxScheduleVersion(
        tenant_id=tenant_id,
        source_id=source_id,
        version_label=f"v-{status}",
        fiscal_year=fiscal_year,
        schedule_type="GENERAL",
        scope_category_code=None,
        status=status,
    )
    db_session.add(version)
    db_session.flush()
    return version


def test_partial_unique_index_blocks_two_approved_same_scope(db_session, tenant, obligation_catalog) -> None:
    source = _make_source(db_session, tenant.id, obligation_catalog["621"].id)
    _make_version(db_session, tenant.id, source.id, status="APPROVED")

    with pytest.raises(IntegrityError):
        _make_version(db_session, tenant.id, source.id, status="APPROVED")


def test_unique_index_allows_different_scopes_both_approved(db_session, tenant, obligation_catalog) -> None:
    source = _make_source(db_session, tenant.id, obligation_catalog["621"].id)
    _make_version(db_session, tenant.id, source.id, status="APPROVED", fiscal_year=2026)
    _make_version(db_session, tenant.id, source.id, status="APPROVED", fiscal_year=2027)

    db_session.flush()  # no debe lanzar IntegrityError: son scopes distintos


def test_concurrent_approval_race_is_translated_to_domain_error(
    db_session, tenant, obligation_catalog, admin_user, monkeypatch
) -> None:
    source = _make_source(db_session, tenant.id, obligation_catalog["621"].id)

    # Version que intentaremos aprobar "normalmente".
    pending = _make_version(db_session, tenant.id, source.id, status="PENDING_REVIEW")

    # Simula que OTRA transaccion ya aprobo una version del mismo scope exacto
    # justo despues de que nuestra lectura de "version previa aprobada" se
    # hiciera (carrera real de concurrencia): se inserta directamente,
    # sin pasar por el servicio.
    concurrent_winner = _make_version(db_session, tenant.id, source.id, status="APPROVED")

    # Se establece un punto de guardado durable antes de la carrera: el
    # rollback interno del servicio (mas abajo) solo debe deshacer lo que
    # el propio approve() intente escribir, nunca los datos ya asentados.
    db_session.commit()

    # Forzamos que el servicio no "vea" la version recien aprobada por la
    # otra transaccion (lectura obsoleta), para que intente promover
    # `pending` a APPROVED igualmente y choque con el indice unico parcial
    # al hacer flush.
    monkeypatch.setattr(tax_schedule_repository, "get_approved_version", lambda *args, **kwargs: None)

    with pytest.raises(ConcurrentApprovalConflictError):
        tax_schedule_service.approve(db_session, tenant.id, admin_user.id, pending.id, "Aprobado")

    # La sesion debe seguir siendo utilizable tras el rollback interno del
    # servicio, y el estado de ambas versiones no debe haber quedado
    # corrupto: la ganadora de la carrera sigue APPROVED, la otra sigue
    # PENDING_REVIEW (la aprobacion fallida no dejo cambios a medias).
    db_session.refresh(pending)
    db_session.refresh(concurrent_winner)
    assert pending.status == "PENDING_REVIEW"
    assert concurrent_winner.status == "APPROVED"
