"""POST /classify/batch: límites, errores por elemento, una llamada al motor, atomicidad."""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.application.classification import BatchItemInput, ClassificationService
from app.domain.principal import CredentialType, Principal
from app.infrastructure.engines.registry import EngineRegistry
from app.infrastructure.repositories.uow import SqlUnitOfWork
from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.fakes import FakeClock, RecordingFakeEngine
from tests.helpers import CSRF, Tenant, log_in, make_tenant, stored

URL = "/api/v1/classify/batch"


@pytest.fixture
def tenant(db_session: Session, hasher: Argon2PasswordHasher) -> Tenant:
    return make_tenant(db_session, hasher)


def items(n: int) -> list[dict]:
    return [{"text": f"Solicitud número {i}", "external_ref": f"R-{i}"} for i in range(n)]


def test_fifty_valid_items_in_a_single_engine_call(
    client: TestClient, db_session: Session, tenant: Tenant, llm_engine: RecordingFakeEngine
) -> None:
    response = client.post(URL, json={"items": items(50)}, headers=tenant.key_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "llm"
    assert [r["index"] for r in body["results"]] == list(range(50))
    assert all(r["category"] == "Petición" and r["error"] is None for r in body["results"])
    assert len(llm_engine.calls) == 1 and len(llm_engine.calls[0]) == 50  # una sola llamada
    rows = stored(db_session, tenant.org.id)
    assert len(rows) == 50
    assert {str(r.batch_id) for r in rows} == {body["batch_id"]}
    assert {r.source for r in rows} == {"api"}


def test_history_keeps_batch_order(client: TestClient, db_session: Session, tenant: Tenant) -> None:
    client.post(URL, json={"items": items(5)}, headers=tenant.key_headers)
    rows = list(reversed(stored(db_session, tenant.org.id)))  # del más antiguo al más reciente
    assert [r.external_ref for r in rows] == [f"R-{i}" for i in range(5)]


def test_fifty_one_items_is_413_and_nothing_is_classified(
    client: TestClient, db_session: Session, tenant: Tenant, llm_engine: RecordingFakeEngine
) -> None:
    response = client.post(URL, json={"items": items(51)}, headers=tenant.key_headers)
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "batch_too_large"
    assert llm_engine.calls == []
    assert stored(db_session, tenant.org.id) == []


def test_invalid_items_get_their_own_error_and_do_not_stop_the_rest(
    client: TestClient, db_session: Session, tenant: Tenant, llm_engine: RecordingFakeEngine
) -> None:
    payload = {
        "items": [
            {"text": "primero válido"},
            {"text": ""},
            {"text": "   "},
            {"text": "x" * 5001},
            {"text": "con referencia larga", "external_ref": "r" * 101},
            {"text": "  segundo válido  ", "external_ref": "ok"},
        ]
    }
    response = client.post(URL, json=payload, headers=tenant.key_headers)

    assert response.status_code == 200
    results = response.json()["results"]
    assert [r["index"] for r in results] == [0, 1, 2, 3, 4, 5]
    assert [r["error"]["code"] if r["error"] else None for r in results] == [
        None,
        "invalid_text",
        "invalid_text",
        "invalid_text",
        "invalid_external_ref",
        None,
    ]
    assert results[0]["id"] and results[5]["id"]
    assert results[1]["id"] is None and results[1]["category"] is None
    assert llm_engine.calls == [["primero válido", "segundo válido"]]
    assert len(stored(db_session, tenant.org.id)) == 2


def test_all_invalid_items_do_not_call_the_engine(
    client: TestClient, db_session: Session, tenant: Tenant, llm_engine: RecordingFakeEngine
) -> None:
    response = client.post(
        URL, json={"items": [{"text": ""}, {"text": " "}]}, headers=tenant.key_headers
    )
    assert response.status_code == 200
    assert [r["error"]["code"] for r in response.json()["results"]] == ["invalid_text"] * 2
    assert llm_engine.calls == []
    assert stored(db_session, tenant.org.id) == []


def test_engine_failure_is_503_and_saves_nothing_of_the_block(
    client: TestClient, db_session: Session, tenant: Tenant, llm_engine: RecordingFakeEngine
) -> None:
    batch_id = str(uuid4())
    first = client.post(
        URL, json={"items": items(3), "batch_id": batch_id}, headers=tenant.key_headers
    )
    assert first.status_code == 200

    llm_engine.fail = True
    second = client.post(
        URL, json={"items": items(4), "batch_id": batch_id}, headers=tenant.key_headers
    )
    assert second.status_code == 503
    assert second.json()["detail"]["code"] == "llm_unavailable"
    rows = stored(db_session, tenant.org.id)
    assert len(rows) == 3  # los bloques anteriores se conservan


def test_unavailable_explicit_engine_is_503_without_using_the_other(
    client: TestClient,
    tenant: Tenant,
    llm_engine: RecordingFakeEngine,
    classifier_engine: RecordingFakeEngine,
) -> None:
    llm_engine.available = False
    response = client.post(
        URL, json={"items": items(2), "model": "llm"}, headers=tenant.key_headers
    )
    assert response.status_code == 503
    assert classifier_engine.calls == []


def test_batch_id_from_the_request_and_generated(
    client: TestClient, tenant: Tenant, db_session: Session
) -> None:
    own = uuid4()
    r1 = client.post(
        URL, json={"items": items(1), "batch_id": str(own)}, headers=tenant.key_headers
    )
    assert r1.json()["batch_id"] == str(own)
    r2 = client.post(URL, json={"items": items(1)}, headers=tenant.key_headers)
    generated = UUID(r2.json()["batch_id"])
    assert generated != own
    assert {r.batch_id for r in stored(db_session, tenant.org.id)} == {own, generated}


def test_source_is_ui_batch_with_a_session_and_needs_csrf(
    client: TestClient, db_session: Session, tenant: Tenant
) -> None:
    log_in(client, tenant)
    assert client.post(URL, json={"items": items(1)}).status_code == 403
    response = client.post(URL, json={"items": items(2)}, headers=CSRF)
    assert response.status_code == 200
    rows = stored(db_session, tenant.org.id)
    assert {r.source for r in rows} == {"ui_batch"}
    assert {r.user_id for r in rows} == {tenant.user.id}


@pytest.mark.parametrize(
    "body", [{"items": []}, {}, {"items": [{"text": "a"}], "batch_id": "no-es-uuid"}]
)
def test_malformed_requests_are_422(client: TestClient, tenant: Tenant, body: dict) -> None:
    response = client.post(URL, json=body, headers=tenant.key_headers)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


def test_block_latency_is_split_among_valid_items(
    db_session: Session, registry: EngineRegistry, clock: FakeClock, tenant: Tenant
) -> None:
    ticks = iter([10.0, 11.0])  # el bloque tarda 1 s
    service = ClassificationService(
        SqlUnitOfWork(db_session), registry, clock, lambda t: t, timer=lambda: next(ticks)
    )
    principal = Principal(tenant.org.id, CredentialType.API_KEY, api_key_id=tenant.api_key_id)
    service.classify_batch(principal, items=[BatchItemInput(f"t{i}") for i in range(4)])
    assert [r.latency_ms for r in stored(db_session, tenant.org.id)] == [250] * 4
