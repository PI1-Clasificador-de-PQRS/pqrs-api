from fastapi.testclient import TestClient
from pqrs_classifier.testing import FakeEngine
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.config.settings import Settings
from app.infrastructure.engines.registry import EngineRegistry
from app.main import create_app
from tests.fakes import FakeClock


def test_health_is_alive(session_factory: sessionmaker[Session]) -> None:
    client = TestClient(create_app(session_factory=session_factory))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_database_is_reachable_and_local_model_is_loaded(
    session_factory: sessionmaker[Session], settings: Settings, clock: FakeClock
) -> None:
    registry = EngineRegistry(
        settings, clock, classifier=FakeEngine(id="classifier"), llm=FakeEngine(id="llm")
    )
    client = TestClient(create_app(settings, session_factory, registry=registry))
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_ignores_the_llm(
    session_factory: sessionmaker[Session], settings: Settings, clock: FakeClock
) -> None:
    registry = EngineRegistry(
        settings,
        clock,
        classifier=FakeEngine(id="classifier"),
        llm=FakeEngine(id="llm", available=False),
    )
    client = TestClient(create_app(settings, session_factory, registry=registry))
    assert client.get("/ready").status_code == 200


def test_ready_returns_503_when_local_model_is_not_loaded(
    session_factory: sessionmaker[Session], settings: Settings, clock: FakeClock
) -> None:
    registry = EngineRegistry(
        settings, clock, classifier=FakeEngine(id="classifier", available=False)
    )
    client = TestClient(create_app(settings, session_factory, registry=registry))
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "classifier_unavailable"


def test_ready_returns_503_without_a_loaded_engine(
    session_factory: sessionmaker[Session], settings: Settings
) -> None:
    client = TestClient(create_app(settings, session_factory))  # el lifespan no corrió
    assert client.get("/ready").status_code == 503


def test_ready_returns_503_when_database_is_down(settings: Settings, clock: FakeClock) -> None:
    broken = create_engine(
        "postgresql+psycopg://x:x@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    registry = EngineRegistry(settings, clock, classifier=FakeEngine(id="classifier"))
    client = TestClient(create_app(settings, sessionmaker(bind=broken), registry=registry))
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database_unavailable"
