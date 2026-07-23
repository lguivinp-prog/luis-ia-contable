"""DeclarationScheduleResolutionV1 (deterministico, hasheable) vs.
DeclarationScheduleSnapshotV1 (envuelve la resolucion + procedencia). El hash
debe ser estable frente a cambios de procedencia y cambiar si cambia
cualquier campo determinista (ver ADR-0027)."""

import uuid
from datetime import date, datetime

from app.schemas.declaration_snapshot import (
    DeclarationScheduleResolutionV1,
    DeclarationScheduleSnapshotV1,
)


def _make_resolution(**overrides) -> DeclarationScheduleResolutionV1:
    base = dict(
        source_id=uuid.UUID(int=1),
        version_id=uuid.UUID(int=2),
        version_label="v1",
        rule_type="ENTRY",
        entry_id=uuid.UUID(int=3),
        exception_id=None,
        due_date_original=date(2026, 2, 12),
        due_date_effective=date(2026, 2, 12),
        client_category_applied=None,
        ruc_last_digit_used=5,
        normative_reference=None,
        resolver_warnings=[],
    )
    base.update(overrides)
    return DeclarationScheduleResolutionV1(**base)


def test_hash_stable_across_different_provenance() -> None:
    resolution = _make_resolution()
    snapshot_a = DeclarationScheduleSnapshotV1(
        resolution=resolution,
        generated_at=datetime(2026, 1, 1),
        generated_by_user_id=uuid.uuid4(),
        generation_run_id=uuid.uuid4(),
    )
    snapshot_b = DeclarationScheduleSnapshotV1(
        resolution=resolution,
        generated_at=datetime(2027, 6, 6),  # fecha de ejecucion distinta
        generated_by_user_id=uuid.uuid4(),  # usuario distinto
        generation_run_id=None,  # run distinto
    )
    assert snapshot_a.resolution.content_hash() == snapshot_b.resolution.content_hash()


def test_hash_changes_when_deterministic_field_changes() -> None:
    resolution_a = _make_resolution()
    resolution_b = _make_resolution(due_date_effective=date(2026, 2, 13))
    assert resolution_a.content_hash() != resolution_b.content_hash()


def test_hash_changes_when_rule_type_changes() -> None:
    resolution_entry = _make_resolution(rule_type="ENTRY", entry_id=uuid.UUID(int=3), exception_id=None)
    resolution_exception = _make_resolution(
        rule_type="EXCEPTION", entry_id=None, exception_id=uuid.UUID(int=3)
    )
    assert resolution_entry.content_hash() != resolution_exception.content_hash()


def test_canonical_json_is_deterministic_regardless_of_field_order() -> None:
    resolution = _make_resolution()
    hash_1 = resolution.content_hash()
    # Reconstruir con los mismos valores pero pasados en otro orden de kwargs
    resolution_2 = DeclarationScheduleResolutionV1(
        resolver_warnings=[],
        ruc_last_digit_used=5,
        normative_reference=None,
        client_category_applied=None,
        due_date_effective=date(2026, 2, 12),
        due_date_original=date(2026, 2, 12),
        exception_id=None,
        entry_id=uuid.UUID(int=3),
        rule_type="ENTRY",
        version_label="v1",
        version_id=uuid.UUID(int=2),
        source_id=uuid.UUID(int=1),
    )
    assert hash_1 == resolution_2.content_hash()
