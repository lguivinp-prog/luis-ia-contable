"""Resolucion de vencimientos del motor de cronogramas (logica pura, muy
testeada). Ver ADR-0011 (versionado), ADR-0013 (categorias con fecha de
referencia), ADR-0014 (digitos normalizados) y ADR-0015 (especificidad
determinista por tupla, sin sumas de pesos ambiguas)."""

import calendar
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.models.tax_schedule import TaxScheduleVersion
from app.repositories import tax_category_repository, tax_schedule_repository
from app.services.tax_schedule_digits import specificity_of


class NoApplicableScheduleRuleError(Exception):
    pass


class AmbiguousScheduleRuleError(Exception):
    pass


class NoApprovedVersionError(Exception):
    pass


@dataclass(frozen=True)
class ScheduleResolution:
    version_id: uuid.UUID
    version_label: str
    source_name: str
    rule_type: str  # "exception" | "entry"
    rule_id: uuid.UUID
    last_digit: int
    matched_category_code: str | None
    due_date_original: date | None
    due_date_effective: date
    due_date_type: str
    is_extension: bool
    warnings: list[str]


def resolve_reference_date(tax_period: str) -> date:
    """Fecha de referencia para evaluar vigencia de categorias del cliente
    (ver docs/TAX_RULES_MODEL.md): CONVENCION DEL SISTEMA, no una regla
    oficial de SUNAT -- se revisa si aparece una fuente oficial distinta.
    Se usa el ULTIMO dia calendario del periodo. Soporta 'YYYY-MM' (mensual)
    y 'YYYY' (anual)."""
    if len(tax_period) == 4 and tax_period.isdigit():
        year = int(tax_period)
        return date(year, 12, 31)

    if len(tax_period) == 7 and tax_period[4] == "-":
        year, month = int(tax_period[:4]), int(tax_period[5:7])
        last_day = calendar.monthrange(year, month)[1]
        return date(year, month, last_day)

    raise ValueError(f"Formato de periodo tributario no reconocido: {tax_period!r}")


def _specificity_tuple(
    category_code: str | None, digits: list[int] | None, priority: int | None
) -> tuple[int, int, int]:
    """Comparacion determinista por tupla (ADR-0015), NO una suma de pesos:
    (1) categoria especifica antes que general, (2) menos digitos aplicables
    es mas especifico, (3) 'priority' explicito solo como ultimo criterio
    (nunca inventado; solo si la fuente importada lo define)."""
    category_rank = 0 if category_code is not None else 1
    digit_rank = specificity_of(digits)
    priority_rank = priority if priority is not None else 1_000_000
    return (category_rank, digit_rank, priority_rank)


def _matches(
    digits: list[int] | None,
    required_category: str | None,
    last_digit: int,
    client_categories: set[str],
) -> bool:
    if digits is not None and last_digit not in digits:
        return False
    if required_category is not None and required_category not in client_categories:
        return False
    return True


def _select_by_specificity(candidates: list, description: str):
    ranked = sorted(
        candidates,
        key=lambda c: _specificity_tuple(c.required_category_code, c.applicable_last_digits, c.priority),
    )
    best = _specificity_tuple(
        ranked[0].required_category_code, ranked[0].applicable_last_digits, ranked[0].priority
    )
    tied = [
        c
        for c in ranked
        if _specificity_tuple(c.required_category_code, c.applicable_last_digits, c.priority) == best
    ]
    if len(tied) > 1:
        raise AmbiguousScheduleRuleError(
            f"Más de {description} aplica igualmente; no se puede determinar una regla única."
        )
    return tied[0]


def select_applicable_version(
    db: Session,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    fiscal_year: int,
    client_categories: set[str],
) -> TaxScheduleVersion:
    candidates = tax_schedule_repository.list_approved_versions_for_source_year(
        db, tenant_id, source_id, fiscal_year
    )
    matching = [
        v for v in candidates if v.scope_category_code is None or v.scope_category_code in client_categories
    ]
    if not matching:
        raise NoApprovedVersionError(
            f"No hay una versión aprobada del cronograma para el año {fiscal_year}"
        )

    def rank(v: TaxScheduleVersion) -> int:
        return 0 if v.scope_category_code is not None else 1

    ranked = sorted(matching, key=rank)
    best_rank = rank(ranked[0])
    tied = [v for v in ranked if rank(v) == best_rank]
    if len(tied) > 1:
        categories_desc = ", ".join(sorted(client_categories)) or "ninguna"
        raise AmbiguousScheduleRuleError(
            "Más de una versión aprobada aplica igualmente a este contribuyente para el año "
            f"{fiscal_year} (categorías del contribuyente: {categories_desc})"
        )
    return tied[0]


def resolve_due_date(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    source_id: uuid.UUID,
    client_id: uuid.UUID,
    last_digit: int,
    tax_period: str,
) -> ScheduleResolution:
    reference_date = resolve_reference_date(tax_period)
    fiscal_year = reference_date.year
    client_categories = set(
        tax_category_repository.get_active_categories_for_client(
            db, tenant_id, client_id, reference_date
        )
    )

    version = select_applicable_version(db, tenant_id, source_id, fiscal_year, client_categories)
    source = tax_schedule_repository.get_source(db, tenant_id, source_id)
    source_name = source.name if source else ""

    exceptions = tax_schedule_repository.list_exceptions(db, tenant_id, version.id, tax_period)
    matching_exceptions = [
        e
        for e in exceptions
        if _matches(e.applicable_last_digits, e.required_category_code, last_digit, client_categories)
    ]
    if matching_exceptions:
        chosen = _select_by_specificity(matching_exceptions, "una excepción")
        return ScheduleResolution(
            version_id=version.id,
            version_label=version.version_label,
            source_name=source_name,
            rule_type="exception",
            rule_id=chosen.id,
            last_digit=last_digit,
            matched_category_code=chosen.required_category_code,
            due_date_original=None,
            due_date_effective=chosen.due_date_effective,
            due_date_type="EXCEPCION",
            is_extension=False,
            warnings=[f"Se aplicó una excepción: {chosen.reason}"],
        )

    entries = tax_schedule_repository.list_entries(db, tenant_id, version.id, tax_period)
    matching_entries = [
        e
        for e in entries
        if _matches(e.applicable_last_digits, e.required_category_code, last_digit, client_categories)
    ]
    if not matching_entries:
        raise NoApplicableScheduleRuleError(
            f"Ninguna regla del cronograma aprobado aplica al dígito {last_digit} "
            f"en el periodo {tax_period}"
        )

    chosen = _select_by_specificity(matching_entries, "una regla")
    warnings = []
    if chosen.due_date_effective != chosen.due_date_original:
        warnings.append("La fecha vigente difiere de la fecha original (prórroga).")

    return ScheduleResolution(
        version_id=version.id,
        version_label=version.version_label,
        source_name=source_name,
        rule_type="entry",
        rule_id=chosen.id,
        last_digit=last_digit,
        matched_category_code=chosen.required_category_code,
        due_date_original=chosen.due_date_original,
        due_date_effective=chosen.due_date_effective,
        due_date_type=chosen.due_date_type,
        is_extension=chosen.due_date_effective != chosen.due_date_original,
        warnings=warnings,
    )
