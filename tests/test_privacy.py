"""El texto original no llega al motor, a la base ni a los logs."""

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.fakes import RecordingFakeEngine
from tests.helpers import Tenant, make_tenant, stored

EMAIL = "maria.gomez@example.com"
CELULAR = "3101234567"
TEXT = f"Mi correo es {EMAIL} y mi celular es {CELULAR}. Nadie responde mi reclamo."


@pytest.fixture
def tenant(db_session: Session, hasher: Argon2PasswordHasher) -> Tenant:
    return make_tenant(db_session, hasher)


def assert_anonymized(text: str) -> None:
    assert EMAIL not in text and CELULAR not in text
    assert "[CORREO]" in text and "[CELULAR]" in text


def test_single_text_is_anonymized_before_the_engine_and_the_database(
    client: TestClient, db_session: Session, tenant: Tenant, llm_engine: RecordingFakeEngine
) -> None:
    response = client.post("/api/v1/classify", json={"text": TEXT}, headers=tenant.key_headers)
    assert response.status_code == 200

    (received,) = llm_engine.calls[0]
    assert_anonymized(received)
    (row,) = stored(db_session, tenant.org.id)
    assert_anonymized(row.text_anonymized)
    assert row.text_anonymized == received
    assert EMAIL not in response.text and CELULAR not in response.text


def test_batch_texts_are_anonymized(
    client: TestClient, db_session: Session, tenant: Tenant, llm_engine: RecordingFakeEngine
) -> None:
    client.post(
        "/api/v1/classify/batch",
        json={"items": [{"text": TEXT}, {"text": TEXT}]},
        headers=tenant.key_headers,
    )
    for received in llm_engine.calls[0]:
        assert_anonymized(received)
    for row in stored(db_session, tenant.org.id):
        assert_anonymized(row.text_anonymized)


def test_original_text_never_appears_in_logs(
    client: TestClient,
    tenant: Tenant,
    llm_engine: RecordingFakeEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    headers = tenant.key_headers
    client.post("/api/v1/classify", json={"text": TEXT}, headers=headers)
    client.post("/api/v1/classify/batch", json={"items": [{"text": TEXT}]}, headers=headers)
    client.post("/api/v1/classify", json={"text": TEXT, "model": "gpt"}, headers=headers)  # 422
    llm_engine.fail = True
    client.post("/api/v1/classify", json={"text": TEXT}, headers=headers)  # 503
    client.post("/api/v1/classify/batch", json={"items": [{"text": TEXT}]}, headers=headers)

    assert caplog.records
    for record in caplog.records:
        rendered = record.getMessage() + " " + str(getattr(record, "exc_text", "") or "")
        assert (
            EMAIL not in rendered and CELULAR not in rendered and "Nadie responde" not in rendered
        )
        for value in vars(record).values():
            assert EMAIL not in str(value) and CELULAR not in str(value)
