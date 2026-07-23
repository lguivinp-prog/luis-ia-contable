import pytest

from app.services.declaration_document_service import compute_document_status


@pytest.mark.parametrize(
    "item_statuses,expected",
    [
        ([], "NOT_CONFIGURED"),
        (["NOT_APPLICABLE", "NOT_APPLICABLE"], "NOT_APPLICABLE"),
        (["ACCEPTED", "OBSERVED"], "OBSERVED"),
        (["ACCEPTED", "NOT_APPLICABLE", "ACCEPTED"], "COMPLETE"),
        (["NOT_REQUESTED", "REQUESTED"], "PENDING"),
        (["ACCEPTED", "REQUESTED"], "PARTIAL"),
        (["RECEIVED", "NOT_REQUESTED"], "PARTIAL"),
    ],
)
def test_compute_document_status(item_statuses: list[str], expected: str) -> None:
    assert compute_document_status(item_statuses) == expected
