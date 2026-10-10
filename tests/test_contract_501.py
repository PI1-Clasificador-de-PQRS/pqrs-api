"""Contrato v0: las rutas de la parte 2 autentican y luego responden 501."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.factories import seed_api_key, seed_login_user

CSRF = {"X-PQRS-CSRF": "1"}
PASSWORD = "contraseña-segura-1"
MARKER = "texto-original-de-una-pqrs-MARCADOR"

SESSION_OR_KEY_ROUTES = [
    ("POST", "/api/v1/classify", {"text": "Quiero información sobre mi trámite"}),
    ("POST", "/api/v1/classify/batch", {"items": [{"text": "uno"}, {"text": "dos"}]}),
]
SESSION_ONLY_ROUTES = [
    ("GET", "/api/v1/classifications", None),
    ("GET", f"/api/v1/classifications/{uuid4()}", None),
    ("GET", "/api/v1/metrics/summary", None),
]
ALL_ROUTES = SESSION_OR_KEY_ROUTES + SESSION_ONLY_ROUTES


def call(client: TestClient, method: str, path: str, body, headers=None):
    return client.request(method, path, json=body, headers=headers)


def log_in(client: TestClient, db: Session, hasher: Argon2PasswordHasher):
    org, _ = seed_login_user(db, hasher)
    client.post("/api/v1/auth/login", json={"email": "ana@example.com", "password": PASSWORD})
    return org


@pytest.mark.parametrize(("method", "path", "body"), ALL_ROUTES)
def test_without_credentials_is_401(client: TestClient, method: str, path: str, body) -> None:
    response = call(client, method, path, body)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "missing_credentials"


@pytest.mark.parametrize(("method", "path", "body"), ALL_ROUTES)
def test_with_session_is_501(
    client: TestClient,
    db_session: Session,
    hasher: Argon2PasswordHasher,
    method: str,
    path: str,
    body,
) -> None:
    log_in(client, db_session, hasher)
    response = call(client, method, path, body, CSRF)
    assert response.status_code == 501
    assert response.json()["detail"]["code"] == "not_implemented"


@pytest.mark.parametrize(("method", "path", "body"), [r for r in ALL_ROUTES if r[0] == "POST"])
def test_post_with_session_requires_csrf(
    client: TestClient,
    db_session: Session,
    hasher: Argon2PasswordHasher,
    method: str,
    path: str,
    body,
) -> None:
    log_in(client, db_session, hasher)
    response = call(client, method, path, body)
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_required"


@pytest.mark.parametrize(("method", "path", "body"), SESSION_OR_KEY_ROUTES)
def test_api_key_is_accepted_where_classification_is_allowed(
    client: TestClient,
    db_session: Session,
    hasher: Argon2PasswordHasher,
    method: str,
    path: str,
    body,
) -> None:
    org, _ = seed_login_user(db_session, hasher)
    raw, _key = seed_api_key(db_session, org.id)
    response = call(client, method, path, body, {"X-API-Key": raw})  # sin cabecera CSRF
    assert response.status_code == 501
    assert response.json()["detail"]["code"] == "not_implemented"


@pytest.mark.parametrize(("method", "path", "body"), SESSION_ONLY_ROUTES)
def test_api_key_is_rejected_on_session_only_routes(
    client: TestClient,
    db_session: Session,
    hasher: Argon2PasswordHasher,
    method: str,
    path: str,
    body,
) -> None:
    org, _ = seed_login_user(db_session, hasher)
    raw, _key = seed_api_key(db_session, org.id)
    response = call(client, method, path, body, {"X-API-Key": raw})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "session_required"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/v1/classify", {"text": ""}),
        ("POST", "/api/v1/classify", {"text": MARKER * 200}),  # > 5000 caracteres
        ("POST", "/api/v1/classify", {"text": MARKER, "model": "gpt"}),
        ("POST", "/api/v1/classify", {"text": MARKER, "external_ref": "r" * 101}),
        ("POST", "/api/v1/classify/batch", {"items": []}),
        ("POST", "/api/v1/classify/batch", {"items": [{"text": MARKER}], "batch_id": "no-es-uuid"}),
        ("GET", "/api/v1/classifications?page_size=101", None),
        ("GET", "/api/v1/classifications?page=0", None),
        ("GET", "/api/v1/classifications?category=Otra", None),
        ("GET", "/api/v1/classifications/no-es-uuid", None),
        ("GET", "/api/v1/metrics/summary?granularity=month", None),
        ("GET", "/api/v1/metrics/summary?date_from=ayer", None),
    ],
)
def test_invalid_requests_are_422_without_echoing_input(
    client: TestClient,
    db_session: Session,
    hasher: Argon2PasswordHasher,
    method: str,
    path: str,
    body,
) -> None:
    log_in(client, db_session, hasher)
    response = call(client, method, path, body, CSRF)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    assert MARKER not in response.text


def test_auth_is_checked_before_the_body(client: TestClient) -> None:
    response = client.post("/api/v1/classify", json={"text": ""})
    assert response.status_code == 401


def test_batch_items_are_not_length_validated_by_the_schema(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    """Un elemento vacío no tumba el lote con 422: lo gestionará la parte 2 con su `error`."""
    log_in(client, db_session, hasher)
    response = client.post(
        "/api/v1/classify/batch", json={"items": [{"text": ""}, {"text": "ok"}]}, headers=CSRF
    )
    assert response.status_code == 501
