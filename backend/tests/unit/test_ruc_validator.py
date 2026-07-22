import pytest

from app.services.ruc_validator import (
    InvalidRucError,
    compute_check_digit,
    get_ruc_last_digit,
    is_valid_ruc,
    validate_ruc,
)


def _build_ruc(prefix: str, base8: str) -> str:
    """Construye un RUC matematicamente valido (ficticio) para pruebas."""
    first_ten = prefix + base8
    check_digit = compute_check_digit(first_ten)
    return first_ten + str(check_digit)


@pytest.mark.parametrize("prefix", ["10", "15", "16", "17", "20"])
def test_valid_ruc_for_each_known_prefix(prefix: str) -> None:
    ruc = _build_ruc(prefix, "12345678")
    assert is_valid_ruc(ruc)
    assert validate_ruc(ruc) == ruc


def test_invalid_length_is_rejected() -> None:
    assert not is_valid_ruc("1012345678")  # 10 digitos
    with pytest.raises(InvalidRucError):
        validate_ruc("1012345678")


def test_non_numeric_is_rejected() -> None:
    assert not is_valid_ruc("10A234567B")
    with pytest.raises(InvalidRucError):
        validate_ruc("10A234567B1")


def test_unknown_prefix_is_rejected() -> None:
    ruc = _build_ruc("99", "12345678")
    assert not is_valid_ruc(ruc)
    with pytest.raises(InvalidRucError):
        validate_ruc(ruc)


def test_wrong_check_digit_is_rejected() -> None:
    ruc = _build_ruc("20", "12345678")
    wrong_last_digit = "0" if ruc[10] != "0" else "1"
    tampered = ruc[:10] + wrong_last_digit
    assert not is_valid_ruc(tampered)
    with pytest.raises(InvalidRucError):
        validate_ruc(tampered)


def test_get_ruc_last_digit() -> None:
    ruc = _build_ruc("20", "12345678")
    assert get_ruc_last_digit(ruc) == int(ruc[10])


def test_compute_check_digit_requires_ten_digits() -> None:
    with pytest.raises(InvalidRucError):
        compute_check_digit("123")
