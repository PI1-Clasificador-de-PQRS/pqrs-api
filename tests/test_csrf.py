from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.factories import seed_login_user


def test_cookie_post_without_csrf_header_is_forbidden(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    seed_login_user(db_session, hasher)
    client.post(
        "/api/v1/auth/login", json={"email": "ana@example.com", "password": "contraseña-segura-1"}
    )

    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_required"

    wrong = client.post("/api/v1/auth/logout", headers={"X-PQRS-CSRF": "0"})
    assert wrong.status_code == 403

    # Con la cabecera pasa y la sesión sigue viva hasta entonces.
    assert client.get("/api/v1/me").status_code == 200
    assert client.post("/api/v1/auth/logout", headers={"X-PQRS-CSRF": "1"}).status_code == 204


def test_get_does_not_require_csrf_header(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    seed_login_user(db_session, hasher)
    client.post(
        "/api/v1/auth/login", json={"email": "ana@example.com", "password": "contraseña-segura-1"}
    )
    assert client.get("/api/v1/me").status_code == 200


def test_login_is_exempt_from_csrf(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    seed_login_user(db_session, hasher)
    response = client.post(
        "/api/v1/auth/login", json={"email": "ana@example.com", "password": "contraseña-segura-1"}
    )
    assert response.status_code == 200
