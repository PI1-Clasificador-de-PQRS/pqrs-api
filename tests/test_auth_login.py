import hashlib

import pytest
from argon2 import PasswordHasher as Argon2
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.infrastructure.config.settings import Settings
from app.infrastructure.db.models import SessionModel, UserModel
from app.infrastructure.security.passwords import Argon2PasswordHasher
from app.main import create_app
from tests.factories import seed_login_user

LOGIN = "/api/v1/auth/login"
PASSWORD = "contraseña-segura-1"


def login(client: TestClient, email: str = "ana@example.com", password: str = PASSWORD):
    return client.post(LOGIN, json={"email": email, "password": password})


def test_valid_login_sets_cookie_and_stores_only_the_hash(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    org, user = seed_login_user(db_session, hasher)
    response = login(client)

    assert response.status_code == 200
    assert response.json() == {
        "user": {"id": str(user.id), "email": "ana@example.com", "name": "Ana Pérez"},
        "organization": {"id": str(org.id), "name": "Org Login"},
    }

    set_cookie = response.headers["set-cookie"]
    flags = [part.strip().lower() for part in set_cookie.split(";")]
    assert "httponly" in flags
    assert "secure" in flags
    assert "samesite=lax" in flags
    assert "path=/" in flags
    assert not any(f.startswith(("max-age", "expires")) for f in flags)

    token = client.cookies.get("pqrs_session")
    assert token and len(token) >= 43  # token_urlsafe(32)
    stored = db_session.scalars(select(SessionModel)).all()
    assert len(stored) == 1
    assert stored[0].token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert stored[0].user_id == user.id
    # El token en claro no está en ninguna columna de la tabla.
    assert (
        db_session.scalar(text("SELECT count(*) FROM sessions WHERE token_hash = :t"), {"t": token})
        == 0
    )


def test_login_is_case_insensitive_on_email(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    seed_login_user(db_session, hasher)
    assert login(client, email="  ANA@Example.COM ").status_code == 200


def _failure_cases(db: Session, hasher: Argon2PasswordHasher) -> dict[str, tuple[str, str]]:
    seed_login_user(db, hasher, email="ok@example.com")
    seed_login_user(db, hasher, email="inactivo@example.com", org_name="O2", user_active=False)
    seed_login_user(db, hasher, email="orginactiva@example.com", org_name="O3", org_active=False)
    return {
        "wrong_password": ("ok@example.com", "contraseña-incorrecta-1"),
        "unknown_user": ("nadie@example.com", PASSWORD),
        "inactive_user": ("inactivo@example.com", PASSWORD),
        "inactive_org": ("orginactiva@example.com", PASSWORD),
    }


def test_all_login_failures_are_indistinguishable(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    cases = _failure_cases(db_session, hasher)
    responses = {name: login(client, *creds) for name, creds in cases.items()}

    for name, response in responses.items():
        assert response.status_code == 401, name
        assert response.json()["detail"]["code"] == "invalid_credentials", name
        assert "set-cookie" not in response.headers, name
    bodies = {r.text for r in responses.values()}
    assert len(bodies) == 1, "los cuatro fallos deben tener exactamente el mismo cuerpo"
    assert db_session.scalar(text("SELECT count(*) FROM sessions")) == 0


def test_unknown_user_is_verified_against_a_dummy_hash(
    client: TestClient, app, hasher: Argon2PasswordHasher
) -> None:
    calls: list[str] = []
    original = hasher.verify_dummy
    hasher.verify_dummy = lambda password: (calls.append(password), original(password))[1]  # type: ignore[method-assign]
    try:
        assert login(client, email="nadie@example.com").status_code == 401
    finally:
        hasher.verify_dummy = original  # type: ignore[method-assign]
    assert calls == [PASSWORD]


def test_too_many_failed_attempts_returns_429(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    seed_login_user(db_session, hasher)
    for _ in range(5):
        assert login(client, password="incorrecta-incorrecta").status_code == 401
    blocked = login(client, password="incorrecta-incorrecta")
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "too_many_attempts"
    assert blocked.headers["retry-after"] == str(15 * 60)
    # Bloqueado incluso con la contraseña correcta.
    assert login(client).status_code == 429


def test_failures_expire_with_the_window(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher, clock
) -> None:
    seed_login_user(db_session, hasher)
    for _ in range(5):
        login(client, password="incorrecta-incorrecta")
    assert login(client).status_code == 429
    clock.advance(minutes=16)
    assert login(client).status_code == 200


def test_ip_limit_uses_forwarded_for_only_when_trusted(
    db_session: Session, session_factory, clock, hasher: Argon2PasswordHasher, settings: Settings
) -> None:
    seed_login_user(db_session, hasher)
    trusted = settings.model_copy(update={"trust_forwarded_for": True})
    client = TestClient(
        create_app(trusted, session_factory, clock=clock, hasher=hasher),
        base_url="https://testserver",
    )
    for i in range(5):
        r = client.post(
            LOGIN,
            json={"email": f"x{i}@example.com", "password": "incorrecta-incorrecta"},
            headers={"X-Forwarded-For": "1.1.1.1, 10.0.0.1"},
        )
        assert r.status_code == 401
    blocked = client.post(
        LOGIN,
        json={"email": "ana@example.com", "password": PASSWORD},
        headers={"X-Forwarded-For": "1.1.1.1"},
    )
    assert blocked.status_code == 429
    other_ip = client.post(
        LOGIN,
        json={"email": "ana@example.com", "password": PASSWORD},
        headers={"X-Forwarded-For": "2.2.2.2"},
    )
    assert other_ip.status_code == 200


def test_login_rehashes_outdated_password_hash(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    _, user = seed_login_user(db_session, hasher)
    weak = Argon2(time_cost=1, memory_cost=8, parallelism=1).hash(PASSWORD)
    db_session.execute(text("UPDATE users SET password_hash = :h"), {"h": weak})
    db_session.commit()

    assert login(client).status_code == 200

    db_session.expire_all()
    new_hash = db_session.scalar(select(UserModel.password_hash).where(UserModel.id == user.id))
    assert new_hash != weak
    assert not hasher.needs_rehash(new_hash)  # type: ignore[arg-type]
    assert hasher.verify(new_hash, PASSWORD)  # type: ignore[arg-type]


@pytest.mark.parametrize("body", [{}, {"email": "a@x.com"}, {"email": 1, "password": "x"}])
def test_login_with_malformed_body_is_invalid_request(client: TestClient, body: dict) -> None:
    response = client.post(LOGIN, json=body)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
