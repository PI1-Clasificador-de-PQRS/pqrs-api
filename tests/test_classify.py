"""POST /classify: sesión o API key, selección de motor, validación y guardado."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.fakes import FakeClock, RecordingFakeEngine
from tests.helpers import CSRF, Tenant, log_in, make_tenant, stored

URL = "/api/v1/classify"
TEXT = "Quiero información sobre el estado de mi trámite"


@pytest.fixture
def tenant(db_session: Session, hasher: Argon2PasswordHasher) -> Tenant:
    return make_tenant(db_session, hasher)


def test_requires_credentials_before_validating_the_body(client: TestClient) -> None:
    assert client.post(URL, json={"text": ""}).status_code == 401


def test_session_classifies_and_stores_with_source_ui(
    client: TestClient, db_session: Session, tenant: Tenant, clock: FakeClock
) -> None:
    log_in(client, tenant)
    response = client.post(URL, json={"text": TEXT, "external_ref": "RAD-1"}, headers=CSRF)

    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "Petición"
    assert body["model"] == "llm"  # sin `model`, el LLM si está disponible
    assert body["model_version"] == "fake-llm-1"
    assert body["timestamp"].startswith("2026-01-15T12:00:00")
    (row,) = stored(db_session, tenant.org.id)
    assert str(row.id) == body["id"]
    assert row.source == "ui"
    assert (row.user_id, row.api_key_id) == (tenant.user.id, None)
    assert (row.engine, row.model_version, row.prompt_version) == (
        "llm",
        "fake-llm-1",
        "fake-prompt-1",
    )
    assert row.external_ref == "RAD-1"
    assert row.batch_id is None
    assert row.latency_ms >= 0
    assert row.created_at == clock.now()


def test_session_requires_csrf(client: TestClient, tenant: Tenant) -> None:
    log_in(client, tenant)
    response = client.post(URL, json={"text": TEXT})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "csrf_required"


def test_api_key_classifies_with_source_api_and_no_csrf(
    client: TestClient, db_session: Session, tenant: Tenant
) -> None:
    response = client.post(URL, json={"text": TEXT}, headers=tenant.key_headers)
    assert response.status_code == 200
    (row,) = stored(db_session, tenant.org.id)
    assert row.source == "api"
    assert (row.user_id, row.api_key_id) == (None, tenant.api_key_id)


def test_explicit_classifier(
    client: TestClient,
    db_session: Session,
    tenant: Tenant,
    llm_engine: RecordingFakeEngine,
    classifier_engine: RecordingFakeEngine,
) -> None:
    response = client.post(
        URL, json={"text": TEXT, "model": "classifier"}, headers=tenant.key_headers
    )
    body = response.json()
    assert (body["model"], body["model_version"]) == ("classifier", "fake-classifier-1")
    assert llm_engine.calls == []
    assert len(classifier_engine.calls) == 1
    (row,) = stored(db_session, tenant.org.id)
    assert (row.engine, row.prompt_version) == ("classifier", None)


def test_omitted_model_falls_to_classifier_when_llm_is_unavailable(
    client: TestClient,
    tenant: Tenant,
    llm_engine: RecordingFakeEngine,
    classifier_engine: RecordingFakeEngine,
) -> None:
    llm_engine.available = False
    response = client.post(URL, json={"text": TEXT}, headers=tenant.key_headers)
    assert response.status_code == 200
    assert response.json()["model"] == "classifier"
    assert llm_engine.calls == []


def test_explicit_unavailable_engine_is_503_without_using_the_other(
    client: TestClient,
    db_session: Session,
    tenant: Tenant,
    llm_engine: RecordingFakeEngine,
    classifier_engine: RecordingFakeEngine,
) -> None:
    llm_engine.available = False
    response = client.post(URL, json={"text": TEXT, "model": "llm"}, headers=tenant.key_headers)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "llm_unavailable"
    assert classifier_engine.calls == []
    assert stored(db_session, tenant.org.id) == []

    llm_engine.available = True
    classifier_engine.available = False
    response = client.post(
        URL, json={"text": TEXT, "model": "classifier"}, headers=tenant.key_headers
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "classifier_unavailable"
    assert llm_engine.calls == []


def test_explicit_engine_that_fails_is_503_without_using_the_other(
    client: TestClient,
    db_session: Session,
    tenant: Tenant,
    llm_engine: RecordingFakeEngine,
    classifier_engine: RecordingFakeEngine,
) -> None:
    llm_engine.fail = True
    response = client.post(URL, json={"text": TEXT, "model": "llm"}, headers=tenant.key_headers)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "llm_unavailable"
    assert classifier_engine.calls == []
    assert stored(db_session, tenant.org.id) == []


def test_engine_chosen_by_default_that_fails_is_503_not_a_retry_with_the_other(
    client: TestClient,
    tenant: Tenant,
    llm_engine: RecordingFakeEngine,
    classifier_engine: RecordingFakeEngine,
) -> None:
    llm_engine.fail = True
    response = client.post(URL, json={"text": TEXT}, headers=tenant.key_headers)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "llm_unavailable"
    assert classifier_engine.calls == []


def test_open_circuit_returns_retry_after(
    client: TestClient, tenant: Tenant, llm_engine: RecordingFakeEngine
) -> None:
    llm_engine.fail = True
    for _ in range(3):
        client.post(URL, json={"text": TEXT, "model": "llm"}, headers=tenant.key_headers)
    llm_engine.fail = False
    response = client.post(URL, json={"text": TEXT, "model": "llm"}, headers=tenant.key_headers)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "60"
    assert llm_engine.calls == [[TEXT]] * 3  # abierto: ya no se llama al motor


@pytest.mark.parametrize(
    "body",
    [
        {"text": ""},
        {"text": "   \n\t "},
        {"text": "x" * 5001},
        {"text": TEXT, "model": "gpt"},
        {"text": TEXT, "external_ref": "r" * 101},
        {"external_ref": "r"},
    ],
)
def test_invalid_requests_are_422(client: TestClient, tenant: Tenant, body: dict) -> None:
    response = client.post(URL, json=body, headers=tenant.key_headers)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


def test_length_is_counted_after_stripping_and_the_text_is_stored_stripped(
    client: TestClient, db_session: Session, tenant: Tenant, classifier_engine: RecordingFakeEngine
) -> None:
    text = "  " + "a" * 5000 + " \n"
    response = client.post(
        URL, json={"text": text, "model": "classifier"}, headers=tenant.key_headers
    )
    assert response.status_code == 200
    assert classifier_engine.calls == [["a" * 5000]]
    (row,) = stored(db_session, tenant.org.id)
    assert row.text_anonymized == "a" * 5000


def test_organization_comes_from_the_credential(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher, tenant: Tenant
) -> None:
    other = make_tenant(db_session, hasher, name="Org B", email="bea@example.com")
    client.post(URL, json={"text": TEXT, "org_id": str(other.org.id)}, headers=tenant.key_headers)
    assert len(stored(db_session, tenant.org.id)) == 1
    assert stored(db_session, other.org.id) == []
