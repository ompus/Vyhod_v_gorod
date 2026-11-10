import pytest
from app.auth.security import hash_password, verify_password, create_access_token, decode_access_token


def test_password_hashing():
    plain = "secret123"
    hashed = hash_password(plain)
    assert hashed != plain
    assert "$" in hashed
    assert verify_password(plain, hashed) is True
    assert verify_password("wrong_password", hashed) is False


def test_jwt_token_flow():
    payload = {"sub": "42", "username": "ivan"}
    token = create_access_token(payload)
    assert isinstance(token, str)

    decoded = decode_access_token(token)
    assert decoded is not None
    assert decoded["sub"] == "42"
    assert decoded["username"] == "ivan"


def test_invalid_token():
    assert decode_access_token("invalid.token.signature") is None
