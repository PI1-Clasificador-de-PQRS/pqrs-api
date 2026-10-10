"""GET /models y selección de motor (sin respaldo automático)."""

import pytest
from fastapi.testclient import TestClient
from pqrs_classifier.engines.base import EngineError, EngineUnavailableError
from pqrs_classifier.testing import FakeEngine
from sqlalchemy.orm import Session

from app.domain.errors import ErrorCode, ServiceUnavailable
from app.infrastructure.config.settings import Settings
from app.infrastructure.engines.registry import EngineRegistry
from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.factories import seed_api_key, seed_login_user
from tests.fakes import FakeClock, ScriptedEngine, fail


def by_id(response) -> dict:
    return {m["id"]: m for m in response.json()}


def test_requires_credentials(client: TestClient) -> None:
    assert client.get("/api/v1/models").status_code == 401


def test_lists_both_engines_with_evaluation(
    client: TestClient, db_session: Session, hasher: Argon2PasswordHasher
) -> None:
    org, _ = seed_login_user(db_session, hasher)
    raw, _ = seed_api_key(db_session, org.id)
    response = client.get("/api/v1/models", headers={"X-API-Key": raw})
    assert response.status_code == 200
    assert [m["id"] for m in response.json()] == ["llm", "classifier"]
    models = by_id(response)
    assert models["llm"]["available"] is True
    assert models["llm"]["reason"] is None and models["llm"]["retry_after"] is None
    assert models["llm"]["evaluation"]["f1_macro"] == pytest.approx(0.8329)
    assert models["classifier"]["evaluation"]["f1_macro"] == pytest.approx(0.7776)


def test_llm_not_configured(
    client: TestClient,
    llm_engine: FakeEngine,
    db_session: Session,
    hasher: Argon2PasswordHasher,
) -> None:
    llm_engine.available = False
    org, _ = seed_login_user(db_session, hasher)
    raw, _ = seed_api_key(db_session, org.id)
    models = by_id(client.get("/api/v1/models", headers={"X-API-Key": raw}))
    assert models["llm"]["available"] is False
    assert models["llm"]["reason"] == "not_configured"
    assert models["classifier"]["available"] is True


def test_classifier_not_loaded(
    client: TestClient,
    classifier_engine: FakeEngine,
    db_session: Session,
    hasher: Argon2PasswordHasher,
) -> None:
    classifier_engine.available = False
    org, _ = seed_login_user(db_session, hasher)
    raw, _ = seed_api_key(db_session, org.id)
    models = by_id(client.get("/api/v1/models", headers={"X-API-Key": raw}))
    assert models["classifier"]["available"] is False
    assert models["classifier"]["reason"] == "not_loaded"


# --- selección de motor y circuito (directamente sobre el registro) -----------------------------


def make_registry(settings: Settings, clock: FakeClock, llm, classifier) -> EngineRegistry:
    return EngineRegistry(settings, clock, llm=llm, classifier=classifier)


def test_explicit_available_engine_is_selected(settings: Settings, clock: FakeClock) -> None:
    registry = make_registry(settings, clock, FakeEngine(id="llm"), FakeEngine(id="classifier"))
    assert registry.select("llm") == "llm"
    assert registry.select("classifier") == "classifier"


def test_explicit_unavailable_engine_is_503_and_does_not_use_the_other(
    settings: Settings, clock: FakeClock
) -> None:
    llm = FakeEngine(id="llm", available=False)
    classifier = FakeEngine(id="classifier")
    registry = make_registry(settings, clock, llm, classifier)
    with pytest.raises(ServiceUnavailable) as exc:
        registry.select("llm")
    assert exc.value.code == ErrorCode.LLM_UNAVAILABLE

    classifier.available = False
    llm.available = True
    with pytest.raises(ServiceUnavailable) as exc:
        registry.select("classifier")
    assert exc.value.code == ErrorCode.CLASSIFIER_UNAVAILABLE


def test_explicit_engine_that_fails_is_503_and_does_not_use_the_other(
    settings: Settings, clock: FakeClock
) -> None:
    classifier = ScriptedEngine("classifier")
    llm = ScriptedEngine("llm")
    llm.script = [fail()]
    registry = make_registry(settings, clock, llm, classifier)
    assert registry.select("llm") == "llm"
    with pytest.raises(ServiceUnavailable) as exc:
        registry.classify("llm", "texto")
    assert exc.value.code == ErrorCode.LLM_UNAVAILABLE
    assert classifier.calls == []

    classifier.script = [fail()]
    with pytest.raises(ServiceUnavailable) as exc:
        registry.classify_batch("classifier", ["texto"])
    assert exc.value.code == ErrorCode.CLASSIFIER_UNAVAILABLE
    assert llm.calls == [["texto"]]  # el LLM no recibió el texto del clasificador


def test_unexpected_engine_exception_is_503_without_leaking_its_message(
    settings: Settings, clock: FakeClock
) -> None:
    llm = ScriptedEngine("llm")
    llm.script = [RuntimeError("texto-original-secreto")]
    registry = make_registry(settings, clock, llm, FakeEngine(id="classifier"))
    with pytest.raises(ServiceUnavailable) as exc:
        registry.classify("llm", "x")
    assert "secreto" not in exc.value.message


def test_omitted_model_prefers_llm_when_available(settings: Settings, clock: FakeClock) -> None:
    registry = make_registry(settings, clock, FakeEngine(id="llm"), FakeEngine(id="classifier"))
    assert registry.select(None) == "llm"


def test_omitted_model_uses_classifier_when_llm_is_unavailable(
    settings: Settings, clock: FakeClock
) -> None:
    registry = make_registry(
        settings, clock, FakeEngine(id="llm", available=False), FakeEngine(id="classifier")
    )
    assert registry.select(None) == "classifier"


def test_omitted_model_with_both_unavailable_is_classifier_unavailable(
    settings: Settings, clock: FakeClock
) -> None:
    registry = make_registry(
        settings,
        clock,
        FakeEngine(id="llm", available=False),
        FakeEngine(id="classifier", available=False),
    )
    with pytest.raises(ServiceUnavailable) as exc:
        registry.select(None)
    assert exc.value.code == ErrorCode.CLASSIFIER_UNAVAILABLE


def test_failure_after_selection_does_not_switch_engine(
    settings: Settings, clock: FakeClock
) -> None:
    llm = ScriptedEngine("llm")
    llm.script = [fail()]
    classifier = ScriptedEngine("classifier")
    registry = make_registry(settings, clock, llm, classifier)
    chosen = registry.select(None)
    assert chosen == "llm"
    with pytest.raises(ServiceUnavailable):
        registry.classify(chosen, "x")
    assert classifier.calls == []


def test_llm_circuit_opens_after_three_failures_and_models_reflects_it(
    settings: Settings, clock: FakeClock
) -> None:
    llm = ScriptedEngine("llm")
    llm.script = [fail(), fail(), fail()]
    registry = make_registry(settings, clock, llm, FakeEngine(id="classifier"))
    for _ in range(3):
        with pytest.raises(ServiceUnavailable):
            registry.classify("llm", "x")
    status = {s.id: s for s in registry.status()}["llm"]
    assert (status.available, status.reason, status.retry_after) == (
        False,
        "temporarily_unavailable",
        60,
    )
    calls = len(llm.calls)
    with pytest.raises(ServiceUnavailable) as exc:
        registry.select("llm")
    assert exc.value.retry_after == 60
    assert registry.select(None) == "classifier"  # elección previa a la llamada
    assert len(llm.calls) == calls  # abierto: ni se llama al motor


def test_llm_rate_limit_opens_and_respects_retry_after(
    settings: Settings, clock: FakeClock
) -> None:
    llm = ScriptedEngine("llm")
    llm.script = [EngineUnavailableError("429", reason="rate_limited", retry_after=120)]
    registry = make_registry(settings, clock, llm, FakeEngine(id="classifier"))
    with pytest.raises(ServiceUnavailable) as exc:
        registry.classify("llm", "x")
    assert exc.value.retry_after == 120
    status = {s.id: s for s in registry.status()}["llm"]
    assert status.reason == "temporarily_unavailable" and status.retry_after == 120
    clock.advance(seconds=119)
    assert not {s.id: s for s in registry.status()}["llm"].available
    clock.advance(seconds=2)
    assert {s.id: s for s in registry.status()}["llm"].available


def test_half_open_success_closes_and_failure_reopens(settings: Settings, clock: FakeClock) -> None:
    llm = ScriptedEngine("llm")
    llm.script = [EngineUnavailableError("429", reason="rate_limited"), EngineError("x")]
    registry = make_registry(settings, clock, llm, FakeEngine(id="classifier"))
    with pytest.raises(ServiceUnavailable):
        registry.classify("llm", "x")
    clock.advance(seconds=61)

    with pytest.raises(ServiceUnavailable):  # el sondeo falla: se abre otra vez
        registry.classify("llm", "x")
    assert not {s.id: s for s in registry.status()}["llm"].available

    clock.advance(seconds=61)
    assert registry.classify("llm", "x").engine == "llm"  # el sondeo funciona: se cierra
    assert {s.id: s for s in registry.status()}["llm"].available
    assert registry.breaker.state == "closed"


def test_models_endpoint_shows_open_circuit(
    client: TestClient,
    registry: EngineRegistry,
    clock: FakeClock,
    db_session: Session,
    hasher: Argon2PasswordHasher,
) -> None:
    registry.breaker.record_failure(EngineUnavailableError("429", reason="rate_limited"))
    org, _user = seed_login_user(db_session, hasher)
    raw, _ = seed_api_key(db_session, org.id)
    llm = by_id(client.get("/api/v1/models", headers={"X-API-Key": raw}))["llm"]
    assert llm["available"] is False
    assert llm["reason"] == "temporarily_unavailable"
    assert llm["retry_after"] == 60
