import pytest

from app.core.identifier import InvalidIdentifierError, normalize_login_identifier


def test_case_and_whitespace_variations_collapse_to_same_key() -> None:
    variants = ["ADMIN@DOMINIO.COM", "admin@dominio.com", " admin@dominio.com ", "Admin@Dominio.Com"]
    normalized = {normalize_login_identifier(v) for v in variants}
    assert normalized == {"admin@dominio.com"}


def test_rejects_empty_identifier() -> None:
    with pytest.raises(InvalidIdentifierError):
        normalize_login_identifier("   ")


def test_rejects_too_long_identifier() -> None:
    too_long = "a" * 250 + "@x.com"
    with pytest.raises(InvalidIdentifierError):
        normalize_login_identifier(too_long)


def test_rejects_control_characters() -> None:
    with pytest.raises(InvalidIdentifierError):
        normalize_login_identifier("admin@dominio.com\x00")
    with pytest.raises(InvalidIdentifierError):
        normalize_login_identifier("admin\ttab@dominio.com")


def test_accepts_reasonable_length_identifier() -> None:
    assert normalize_login_identifier("Usuario.Valido+test@Dominio.COM") == "usuario.valido+test@dominio.com"
