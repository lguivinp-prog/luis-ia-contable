from app.core.security import hash_password, verify_password


def test_hash_password_produces_different_salt_each_time() -> None:
    hash_a = hash_password("Correcta123!")
    hash_b = hash_password("Correcta123!")
    assert hash_a != hash_b


def test_hash_password_uses_argon2id_format() -> None:
    result = hash_password("Correcta123!")
    assert result.startswith("$argon2id$")


def test_verify_password_accepts_correct_password() -> None:
    hashed = hash_password("Correcta123!")
    assert verify_password("Correcta123!", hashed) is True


def test_verify_password_rejects_incorrect_password() -> None:
    hashed = hash_password("Correcta123!")
    assert verify_password("Incorrecta456!", hashed) is False
