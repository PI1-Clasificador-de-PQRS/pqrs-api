import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.infrastructure.logging import JsonFormatter, configure_logging
from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.factories import seed_api_key, seed_login_user

PASSWORD = "Marcador-Contraseña-123"
BODY_MARKER = "texto-original-de-una-pqrs-MARCADOR"
EMAIL = "ana@example.com"


def request_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "pqrs.access"]


def test_one_json_line_per_request_with_required_fields(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="pqrs")
    response = client.get("/health?token=secreto-en-query")

    records = request_records(caplog)
    assert len(records) == 1
    data = json.loads(JsonFormatter().format(records[0]))
    assert data["method"] == "GET"
    assert data["path"] == "/health"  # sin query string
    assert data["status"] == 200
    assert isinstance(data["latency_ms"], int | float)
    assert data["request_id"] == response.headers["x-request-id"]
    assert "secreto-en-query" not in json.dumps(data)


def test_logs_never_contain_bodies_or_credentials(
    client: TestClient,
    db_session: Session,
    hasher: Argon2PasswordHasher,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="pqrs")
    org, _ = seed_login_user(db_session, hasher, email=EMAIL, password=PASSWORD)
    raw_key, _key = seed_api_key(db_session, org.id)

    login = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    cookie = client.cookies.get("pqrs_session")
    assert cookie
    client.get("/api/v1/me")
    client.get("/api/v1/me", headers={"X-API-Key": raw_key})
    client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "otra-" + PASSWORD})
    client.post("/api/v1/auth/login", json={"email": BODY_MARKER, "password": 12345})  # 422
    client.post("/ruta-inexistente", json={"texto": BODY_MARKER})  # 404

    assert login.status_code == 200
    assert len(request_records(caplog)) == 6

    forbidden = [PASSWORD, "otra-" + PASSWORD, cookie, raw_key, BODY_MARKER, EMAIL, "12345"]
    formatter = JsonFormatter()
    for record in caplog.records:
        haystack = " ".join([record.getMessage(), formatter.format(record), str(record.__dict__)])
        for secret in forbidden:
            assert secret not in haystack, f"{secret!r} aparece en un registro de log"


def test_unexpected_errors_log_only_the_exception_type(
    app, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="pqrs")

    @app.get("/_test/boom")
    def boom() -> None:
        raise RuntimeError(f"fallo con {BODY_MARKER}")

    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    response = client.get("/_test/boom")

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "internal_error"
    for record in caplog.records:
        assert BODY_MARKER not in JsonFormatter().format(record)
        assert not record.exc_info
    assert any(getattr(r, "exception_type", None) == "RuntimeError" for r in caplog.records)


def test_formatter_drops_unknown_extra_fields() -> None:
    record = logging.LogRecord("pqrs.x", logging.INFO, __file__, 1, "msg", None, None)
    record.cookie = "pqrs_session=abc"  # type: ignore[attr-defined]
    record.status = 200  # type: ignore[attr-defined]
    data = json.loads(JsonFormatter().format(record))
    assert "cookie" not in data
    assert data["status"] == 200


def test_configure_logging_silences_sql_parameters() -> None:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    try:
        configure_logging("INFO")
        assert logging.getLogger("sqlalchemy.engine").level == logging.WARNING
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers, root.level = saved[0], saved[1]
