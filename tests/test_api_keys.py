from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.api.deps import require_session_or_key
from app.domain.principal import CredentialType, Principal
from app.infrastructure.db.models import ApiKeyModel
from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.factories import seed_api_key, seed_login_user
from tests.fakes import FakeClock

PASSWORD = "contraseña-segura-1"


@pytest.fixture(autouse=True)
def key_routes(app: FastAPI) -> None:
    """Rutas solo de pruebas que usan ``require_session_or_key`` (como hará /classify)."""

    @app.get("/_test/whoami")
    def whoami_get(principal: Annotated[Principal, Depends(require_session_or_key)]) -> dict:
        return {"type": principal.credential_type.value, "org_id": str(principal.org_id)}

    @app.post("/_test/whoami")
    def whoami_post(principal: Annotated[Principal, Depends(require_session_or_key)]) -> dict:
        return {"type": principal.credential_type.value, "org_id": str(principal.org_id)}


def test_valid_key_authenticates_without_csrf_header(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    org, _ = seed_login_user(db_session, hasher)
    raw, key = seed_api_key(db_session, org.id)

    for method in (client.get, client.post):
        response = method("/_test/whoami", headers={"X-API-Key": raw})
        assert response.status_code == 200
        assert response.json() == {"type": CredentialType.API_KEY.value, "org_id": str(org.id)}


def test_unknown_malformed_and_missing_keys(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    seed_login_user(db_session, hasher)
    unknown = client.get("/_test/whoami", headers={"X-API-Key": "pqrs_noexiste"})
    assert unknown.status_code == 401
    assert unknown.json()["detail"]["code"] == "invalid_credentials"

    missing = client.get("/_test/whoami")
    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "missing_credentials"


def test_revoked_key_is_rejected(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher, clock: FakeClock
) -> None:
    from app.infrastructure.repositories.uow import SqlUnitOfWork

    org, _ = seed_login_user(db_session, hasher)
    raw, key = seed_api_key(db_session, org.id)
    assert client.get("/_test/whoami", headers={"X-API-Key": raw}).status_code == 200

    uow = SqlUnitOfWork(db_session)
    assert uow.api_keys.revoke(org.id, key.id, clock.now())
    uow.commit()

    response = client.get("/_test/whoami", headers={"X-API-Key": raw})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"


def test_key_of_inactive_org_is_rejected(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    org, _ = seed_login_user(db_session, hasher, org_active=False)
    raw, _key = seed_api_key(db_session, org.id)
    response = client.get("/_test/whoami", headers={"X-API-Key": raw})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"


def test_key_on_a_session_only_endpoint_is_session_required(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    org, _ = seed_login_user(db_session, hasher)
    raw, _key = seed_api_key(db_session, org.id)

    response = client.get("/api/v1/me", headers={"X-API-Key": raw})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_required"

    logout = client.post("/api/v1/auth/logout", headers={"X-API-Key": raw})
    assert logout.status_code == 401
    assert logout.json()["detail"]["code"] == "session_required"


def test_plaintext_key_is_not_stored_anywhere(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    org, _ = seed_login_user(db_session, hasher)
    raw, key = seed_api_key(db_session, org.id)
    client.get("/_test/whoami", headers={"X-API-Key": raw})

    for table in ("organizations", "users", "sessions", "api_keys", "classifications"):
        rows = db_session.execute(text(f"SELECT row_to_json(t)::text FROM {table} t")).scalars()
        assert not any(raw in row for row in rows), f"clave en claro en {table}"
    stored = db_session.scalars(select(ApiKeyModel)).one()
    assert stored.key_hash != raw
    assert stored.prefix == raw[:12]


def test_last_used_at_is_updated_at_most_once_per_minute(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher, clock: FakeClock
) -> None:
    org, _ = seed_login_user(db_session, hasher)
    raw, key = seed_api_key(db_session, org.id)

    def last_used():
        db_session.expire_all()
        return db_session.scalars(select(ApiKeyModel.last_used_at)).one()

    assert last_used() is None
    client.get("/_test/whoami", headers={"X-API-Key": raw})
    first = last_used()
    assert first == clock.now()

    clock.advance(seconds=30)
    client.get("/_test/whoami", headers={"X-API-Key": raw})
    assert last_used() == first

    clock.advance(seconds=31)
    client.get("/_test/whoami", headers={"X-API-Key": raw})
    assert last_used() == clock.now()


def test_session_cookie_also_works_and_still_needs_csrf(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    seed_login_user(db_session, hasher)
    client.post("/api/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD})

    assert client.get("/_test/whoami").json()["type"] == "session"
    forbidden = client.post("/_test/whoami")
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["code"] == "csrf_required"
    assert client.post("/_test/whoami", headers={"X-PQRS-CSRF": "1"}).status_code == 200


def test_api_key_wins_when_both_credentials_are_sent(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    org, _ = seed_login_user(db_session, hasher)
    raw, _key = seed_api_key(db_session, org.id)
    client.post("/api/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD})

    # POST sin cabecera CSRF: con la clave presente no se evalúa la rama de cookie.
    response = client.post("/_test/whoami", headers={"X-API-Key": raw})
    assert response.status_code == 200
    assert response.json()["type"] == "api_key"
