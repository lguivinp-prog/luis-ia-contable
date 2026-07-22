from app.core.security import generate_session_token, hash_session_token


def test_generated_tokens_are_unique_and_long_enough() -> None:
    token_a = generate_session_token()
    token_b = generate_session_token()
    assert token_a != token_b
    assert len(token_a) >= 32


def test_hash_session_token_is_deterministic() -> None:
    token = generate_session_token()
    assert hash_session_token(token) == hash_session_token(token)


def test_hash_session_token_does_not_reveal_original_token() -> None:
    token = generate_session_token()
    hashed = hash_session_token(token)
    assert token not in hashed
    assert len(hashed) == 64  # hex sha256
