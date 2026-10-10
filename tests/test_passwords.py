import pytest
from argon2 import PasswordHasher as Argon2

from app.application.passwords import validate_password_policy
from app.domain.credentials import (
    API_KEY_PREFIX,
    generate_api_key,
    generate_session_token,
    hash_token,
)
from app.domain.errors import DomainError
from app.infrastructure.security.passwords import Argon2PasswordHasher


def test_hash_is_argon2id_and_verifies() -> None:
    hasher = Argon2PasswordHasher()
    password_hash = hasher.hash("una-contraseña-larga")
    assert password_hash.startswith("$argon2id$")
    assert hasher.verify(password_hash, "una-contraseña-larga")
    assert not hasher.verify(password_hash, "otra-contraseña")


def test_verify_returns_false_for_garbage_hash() -> None:
    assert Argon2PasswordHasher().verify("no-es-un-hash", "x") is False


def test_needs_rehash_detects_weaker_parameters() -> None:
    weak = Argon2(time_cost=1, memory_cost=8, parallelism=1).hash("una-contraseña-larga")
    hasher = Argon2PasswordHasher()
    assert hasher.needs_rehash(weak)
    assert not hasher.needs_rehash(hasher.hash("una-contraseña-larga"))


def test_password_policy_requires_twelve_characters() -> None:
    validate_password_policy("a" * 12)
    with pytest.raises(DomainError) as exc:
        validate_password_policy("a" * 11)
    assert exc.value.code == "invalid_request"


def test_tokens_are_hashed_with_sha256_hex() -> None:
    token = generate_session_token()
    assert len(hash_token(token)) == 64
    assert hash_token(token) != token
    assert hash_token(token) == hash_token(token)


def test_api_key_format() -> None:
    key, prefix = generate_api_key()
    assert key.startswith(API_KEY_PREFIX)
    assert key.startswith(prefix)
    assert len(prefix) == 12
    assert generate_api_key()[0] != key
