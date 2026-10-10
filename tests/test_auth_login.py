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
from tests.conftest import PROXY_HEADERS
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


def proxied_client(
    session_factory, clock, hasher: Argon2PasswordHasher, settings: Settings, **overrides
) -> TestClient:
    """Cliente detrás de un proxy de confianza que agrega una entrada a X-Forwarded-For."""
    configured = settings.model_copy(update={"trusted_proxy_hops": 1, **overrides})
    return TestClient(
        create_app(configured, session_factory, clock=clock, hasher=hasher),
        base_url="https://testserver",
        headers=PROXY_HEADERS,
    )


def login_from(client: TestClient, ip: str, email: str, password: str):
    return client.post(
        LOGIN, json={"email": email, "password": password}, headers={"X-Forwarded-For": ip}
    )


def test_attacker_from_another_ip_cannot_lock_out_the_real_user(
    db_session: Session, session_factory, clock, hasher: Argon2PasswordHasher, settings: Settings
) -> None:
    seed_login_user(db_session, hasher)
    client = proxied_client(session_factory, clock, hasher, settings)

    for _ in range(5):
        assert (
            login_from(client, "9.9.9.9", "ana@example.com", "incorrecta-incorrecta").status_code
            == 401
        )
    assert (
        login_from(client, "9.9.9.9", "ana@example.com", "incorrecta-incorrecta").status_code == 429
    )

    legitimate = login_from(client, "8.8.8.8", "ana@example.com", PASSWORD)
    assert legitimate.status_code == 200


def test_per_ip_limit_applies_across_emails(
    db_session: Session, session_factory, clock, hasher: Argon2PasswordHasher, settings: Settings
) -> None:
    seed_login_user(db_session, hasher)
    client = proxied_client(session_factory, clock, hasher, settings, login_max_attempts_per_ip=4)

    for i in range(4):  # ningún par (email, IP) llega a 5, pero la IP suma 4
        r = login_from(client, "1.1.1.1", f"x{i}@example.com", "incorrecta-incorrecta")
        assert r.status_code == 401
    blocked = login_from(client, "1.1.1.1", "ana@example.com", PASSWORD)
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "too_many_attempts"

    assert login_from(client, "2.2.2.2", "ana@example.com", PASSWORD).status_code == 200


def test_a_forged_forwarded_header_cannot_evade_the_limit(
    db_session: Session, session_factory, clock, hasher: Argon2PasswordHasher, settings: Settings
) -> None:
    seed_login_user(db_session, hasher)
    client = proxied_client(session_factory, clock, hasher, settings)

    # El proxy agrega la IP real (última entrada); lo anterior lo falsifica el atacante.
    for i in range(5):
        r = login_from(client, f"10.0.0.{i}, 9.9.9.9", "ana@example.com", "incorrecta-incorrecta")
        assert r.status_code == 401
    blocked = login_from(client, "10.0.0.99, 9.9.9.9", "ana@example.com", PASSWORD)
    assert blocked.status_code == 429


def test_forwarded_header_is_ignored_without_trusted_hops(
    db_session: Session, client: TestClient, hasher: Argon2PasswordHasher
) -> None:
    seed_login_user(db_session, hasher)
    # Con 0 saltos, rotar la IP en la cabecera no evita el límite: es siempre la misma conexión.
    for i in range(5):
        r = login_from(client, f"10.0.0.{i}", "ana@example.com", "incorrecta-incorrecta")
        assert r.status_code == 401
    assert login_from(client, "10.0.0.200", "ana@example.com", PASSWORD).status_code == 429


def test_successful_login_clears_the_pair_counter(
    db_session: Session, session_factory, clock, hasher: Argon2PasswordHasher, settings: Settings
) -> None:
    seed_login_user(db_session, hasher)
    client = proxied_client(session_factory, clock, hasher, settings)
    for _ in range(4):
        login_from(client, "1.1.1.1", "ana@example.com", "incorrecta-incorrecta")
    assert login_from(client, "1.1.1.1", "ana@example.com", PASSWORD).status_code == 200
    # El par volvió a cero: otros 4 fallos no bloquean.
    for _ in range(4):
        assert (
            login_from(client, "1.1.1.1", "ana@example.com", "incorrecta-incorrecta").status_code
            == 401
        )


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
