"""Límite de tamaño del cuerpo: 413 payload_too_large con y sin Content-Length."""

import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.infrastructure.config.settings import Settings
from app.main import create_app
from tests.conftest import PROXY_HEADERS
from tests.helpers import Tenant, make_tenant

CLASSIFY = "/api/v1/classify"


@pytest.fixture
def small(settings: Settings, session_factory, clock, hasher, registry) -> TestClient:
    configured = settings.model_copy(update={"max_body_bytes": 2048})
    app = create_app(configured, session_factory, clock=clock, hasher=hasher, registry=registry)
    return TestClient(app, base_url="https://testserver", headers=PROXY_HEADERS)


@pytest.fixture
def tenant(db_session, hasher) -> Tenant:
    return make_tenant(db_session, hasher)


def assert_too_large(response) -> None:
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "payload_too_large"


def test_oversized_body_with_content_length(small: TestClient, tenant: Tenant) -> None:
    body = json.dumps({"text": "a" * 3000})
    response = small.post(
        CLASSIFY, content=body, headers={**tenant.key_headers, "Content-Type": "application/json"}
    )
    assert response.request.headers["content-length"] == str(len(body))
    assert_too_large(response)


def test_oversized_body_without_content_length(small: TestClient, tenant: Tenant) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"text": "'
        for _ in range(10):
            yield b"a" * 500
        yield b'"}'

    response = small.post(
        CLASSIFY,
        content=chunks(),
        headers={**tenant.key_headers, "Content-Type": "application/json"},
    )
    assert "content-length" not in response.request.headers
    assert response.request.headers["transfer-encoding"] == "chunked"
    assert_too_large(response)


def test_a_false_content_length_does_not_bypass_the_count(
    small: TestClient, tenant: Tenant
) -> None:
    """Content-Length pequeño pero más bytes de los declarados: se cuenta lo que llega."""
    body = json.dumps({"text": "a" * 3000}).encode()
    response = small.post(
        CLASSIFY,
        content=body,
        headers={**tenant.key_headers, "Content-Type": "application/json", "Content-Length": "100"},
    )
    # El servidor HTTP respeta Content-Length; si el cliente de pruebas lo deja pasar, llega
    # recortado y no es JSON válido (422) en vez de colarse: nunca un 200.
    assert response.status_code in (413, 422)


def test_body_within_the_limit_passes(small: TestClient, tenant: Tenant) -> None:
    response = small.post(CLASSIFY, json={"text": "a" * 1000}, headers=tenant.key_headers)
    assert response.status_code == 200


def test_the_default_limit_is_one_mebibyte(settings: Settings) -> None:
    assert Settings().max_body_bytes == 1_048_576
    assert settings.max_body_bytes == 1_048_576


def test_413_is_checked_before_authentication(small: TestClient) -> None:
    assert_too_large(small.post(CLASSIFY, content=b"x" * 5000))


def test_413_does_not_log_the_body(
    small: TestClient, tenant: Tenant, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    body = json.dumps({"text": "MARCADOR-DE-CUERPO" * 300})
    response = small.post(
        CLASSIFY, content=body, headers={**tenant.key_headers, "Content-Type": "application/json"}
    )
    assert_too_large(response)
    assert "MARCADOR-DE-CUERPO" not in response.text
    for record in caplog.records:
        assert "MARCADOR-DE-CUERPO" not in record.getMessage()
    access = [r for r in caplog.records if r.name == "pqrs.access"]
    assert access and access[-1].status == 413  # type: ignore[attr-defined]


def test_get_requests_are_not_affected(small: TestClient) -> None:
    assert small.get("/health").status_code == 200
