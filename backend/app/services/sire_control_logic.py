"""M4 -- logica PURA y DETERMINISTA del control SIRE (sin base de datos, sin E/S): normalizacion de importes, comparacion SUNAT vs DARWIN/CONTASIS, huella de
la comparacion y codigo resumido para el cockpit. Todos los importes son `Decimal` (jamas `float`). Un dato ausente es DESCONOCIDO (None), nunca cero.

Una comparacion que cuadra NO prueba que los registros esten presentados oficialmente ante SUNAT: es un cuadre numerico entre dos fuentes que aporta el contador."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

CONCEPTS = ("record_count", "base_amount", "igv_amount", "total_amount")
AMOUNT_CONCEPTS = ("base_amount", "igv_amount", "total_amount")
CENT = Decimal("0.01")
MAX_ABS_AMOUNT = Decimal("9999999999999.99")  # numeric(18,2) con margen

# Estados de comparacion (el de importacion fallida lo informan los endpoints de importacion, no se persiste).
NO_SOURCES = "NO_SOURCES"
PARTIAL = "PARTIAL"
MATCH = "MATCH"
DIFFERENCES = "DIFFERENCES"
NOT_COMPARABLE = "NOT_COMPARABLE"
IMPORT_ERROR = "IMPORT_ERROR"
COMPARISON_STATUSES = (NO_SOURCES, PARTIAL, MATCH, DIFFERENCES, NOT_COMPARABLE, IMPORT_ERROR)

DISCLAIMER = (
    "Este control es un cuadre numérico entre dos fuentes aportadas por el contador y su revisión profesional. NO constituye constancia oficial de "
    "presentación tributaria ante SUNAT ni modifica el estado de presentación, determinación, pago o vencimiento."
)


class AmountError(ValueError):
    """Importe invalido (no numerico, no finito, mas de 2 decimales o fuera de rango)."""


def parse_amount(value: object) -> Decimal | None:
    """Convierte a Decimal con exactamente <= 2 decimales. None/'' => desconocido (None). Jamas redondea: mas de 2 decimales es un error."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise AmountError("importe invalido")
    if isinstance(value, float):
        raise AmountError("los importes no pueden llegar como float")
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise AmountError("importe no numerico") from exc
    if not amount.is_finite():
        raise AmountError("importe no finito")
    try:
        quantized = amount.quantize(CENT)
    except InvalidOperation as exc:  # exponentes enormes (1e30)
        raise AmountError("importe fuera de rango") from exc
    if quantized != amount:
        raise AmountError("el importe admite como maximo 2 decimales")
    if abs(quantized) > MAX_ABS_AMOUNT:
        raise AmountError("importe fuera de rango")
    return quantized


def fmt(amount: Decimal | int | None) -> str | None:
    """Representacion estable para JSON/huellas (cadena; nunca float)."""
    if amount is None:
        return None
    if isinstance(amount, Decimal):
        return format(amount.quantize(CENT), "f")
    return str(amount)


@dataclass(frozen=True)
class SourceSnapshot:
    id: str
    origin: str
    version: int
    entry_mode: str
    currency: str
    source_label: str
    source_date: date
    content_hash: str
    record_count: int | None = None
    base_amount: Decimal | None = None
    igv_amount: Decimal | None = None
    total_amount: Decimal | None = None

    def value(self, concept: str):
        return getattr(self, concept)


@dataclass(frozen=True)
class ConceptResult:
    concept: str
    sunat: str | None
    darwin: str | None
    difference: str | None  # DARWIN - SUNAT (siempre la diferencia ORIGINAL, aunque una tolerancia explicita la acepte)
    comparable: bool  # el dato existe en AMBAS fuentes
    matches: bool | None  # None si no es comparable
    exact: bool | None  # igualdad exacta (sin tolerancia)
    within_tolerance: bool  # la diferencia se acepto solo por la tolerancia explicita


@dataclass(frozen=True)
class Comparison:
    status: str
    concepts: tuple[ConceptResult, ...] = ()
    currency_mismatch: bool = False
    single_source: str | None = None  # "SUNAT" | "DARWIN" si solo existe una fuente
    one_sided_concepts: tuple[str, ...] = ()  # conocidos en una sola fuente (jamas se toman como cero)
    tolerance: str | None = None
    tolerance_applied: bool = False
    messages: tuple[str, ...] = field(default_factory=tuple)


def compare(sunat: SourceSnapshot | None, darwin: SourceSnapshot | None, tolerance: Decimal | None = None) -> Comparison:
    """Compara conceptos EQUIVALENTES de dos fuentes del mismo expediente. Exacta por defecto; una tolerancia (solo importes) es explicita y NO oculta la
    diferencia original (`difference`/`exact` siempre reflejan la comparacion exacta)."""
    if sunat is None and darwin is None:
        return Comparison(NO_SOURCES, messages=("NO_SOURCES",))
    if sunat is None or darwin is None:
        only = "SUNAT" if sunat is not None else "DARWIN"
        return Comparison(PARTIAL, single_source=only, messages=(f"ONLY_{only}",), tolerance=fmt(tolerance))
    if sunat.currency != darwin.currency:
        return Comparison(NOT_COMPARABLE, currency_mismatch=True, messages=("CURRENCY_MISMATCH",), tolerance=fmt(tolerance))

    results: list[ConceptResult] = []
    one_sided: list[str] = []
    for concept in CONCEPTS:
        a, b = sunat.value(concept), darwin.value(concept)
        if a is None and b is None:
            continue  # desconocido en ambas: no participa (jamas cero)
        if a is None or b is None:
            one_sided.append(concept)
            results.append(ConceptResult(concept, _s(a), _s(b), None, False, None, None, False))
            continue
        diff = b - a
        exact = diff == 0
        tol_ok = False
        matches = exact
        if not exact and concept in AMOUNT_CONCEPTS and tolerance is not None and abs(diff) <= tolerance:
            matches, tol_ok = True, True
        results.append(ConceptResult(concept, _s(a), _s(b), _s(diff), True, matches, exact, tol_ok))

    compared = [r for r in results if r.comparable]
    if not compared:
        return Comparison(NOT_COMPARABLE, tuple(results), one_sided_concepts=tuple(one_sided), messages=("NO_COMMON_CONCEPT",), tolerance=fmt(tolerance))
    applied = any(r.within_tolerance for r in compared)
    if any(r.matches is False for r in compared):
        status, messages = DIFFERENCES, ("DIFFERENCES",)
    elif one_sided:
        status, messages = PARTIAL, ("ONE_SIDED_CONCEPTS",)
    else:
        status, messages = MATCH, ("MATCH_WITH_TOLERANCE",) if applied else ("MATCH",)
    return Comparison(status, tuple(results), one_sided_concepts=tuple(one_sided), tolerance=fmt(tolerance), tolerance_applied=applied, messages=messages)


def _s(value) -> str | None:
    return fmt(value) if value is not None else None


def comparison_hash(sunat: SourceSnapshot | None, darwin: SourceSnapshot | None, tolerance: Decimal | None) -> str:
    """Huella de las ENTRADAS de la comparacion (fuente SUNAT y DARWIN vigentes + tolerancia). Si cambia cualquiera, la revision vigente queda obsoleta."""
    def one(source: SourceSnapshot | None):
        return None if source is None else [source.id, source.version, source.content_hash]

    payload = json.dumps({"sunat": one(sunat), "darwin": one(darwin), "tolerance": fmt(tolerance)}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def source_content_hash(
    *, component: str, origin: str, currency: str, record_count: int | None, base: Decimal | None, igv: Decimal | None, total: Decimal | None,
    file_sha256: str | None,
) -> str:
    payload = json.dumps(
        {"c": component, "o": origin, "cur": currency, "rc": record_count, "b": fmt(base), "i": fmt(igv), "t": fmt(total), "f": file_sha256},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def snapshot_dict(comparison: Comparison) -> dict:
    """Instantanea JSON-serializable de una comparacion (se guarda con cada revision y alimenta el reporte)."""
    return {
        "status": comparison.status,
        "currency_mismatch": comparison.currency_mismatch,
        "single_source": comparison.single_source,
        "one_sided_concepts": list(comparison.one_sided_concepts),
        "tolerance": comparison.tolerance,
        "tolerance_applied": comparison.tolerance_applied,
        "messages": list(comparison.messages),
        "concepts": [
            {"concept": r.concept, "sunat": r.sunat, "darwin": r.darwin, "difference": r.difference, "comparable": r.comparable, "matches": r.matches,
             "exact": r.exact, "within_tolerance": r.within_tolerance}
            for r in comparison.concepts
        ],
    }


# --- revision profesional ---------------------------------------------------------------------------------------------

DECISION_APPROVED = "APPROVED"
DECISION_OBSERVED = "OBSERVED"
DECISION_REGULARIZE = "REQUIRES_REGULARIZATION"
DECISIONS = (DECISION_APPROVED, DECISION_OBSERVED, DECISION_REGULARIZE)


class ReviewRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_review(decision: str, comparison_status: str, notes: str | None) -> None:
    """APPROVED solo si la comparacion CUADRA; toda decision distinta exige observacion; sin fuentes no hay nada que revisar."""
    if decision not in DECISIONS:
        raise ReviewRuleError("REVIEW_DECISION_INVALID")
    if comparison_status == NO_SOURCES:
        raise ReviewRuleError("REVIEW_NOTHING_TO_REVIEW")
    if decision == DECISION_APPROVED and comparison_status != MATCH:
        raise ReviewRuleError("REVIEW_APPROVAL_REQUIRES_MATCH")
    if decision != DECISION_APPROVED and not (notes and notes.strip()):
        raise ReviewRuleError("REVIEW_NOTES_REQUIRED")


# --- resumen para el cockpit (una celda por componente, sin reemplazar los indicadores juridicos) ----------------------

CONTROL_NONE = "NO_CONTROL"
CONTROL_NEEDS_REVIEW = "NEEDS_REVIEW"


def summary_code(*, exists: bool, status: str, review_decision: str | None, review_stale: bool) -> str:
    """Codigo unico del cockpit. NO es un estado de presentacion: refleja solo el cuadre y la revision del control."""
    if not exists:
        return CONTROL_NONE
    if status in (NO_SOURCES, PARTIAL, NOT_COMPARABLE):
        return status
    if review_decision is None:
        return status  # MATCH | DIFFERENCES sin revision
    if review_stale:
        return CONTROL_NEEDS_REVIEW
    return f"{status}_REVIEWED"
