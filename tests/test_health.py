from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.main import create_app


def test_health_is_alive(session_factory: sessionmaker[Session]) -> None:
    client = TestClient(create_app(session_factory=session_factory))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_database_is_reachable(session_factory: sessionmaker[Session]) -> None:
    client = TestClient(create_app(session_factory=session_factory))
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_returns_503_when_database_is_down() -> None:
    broken = create_engine(
        "postgresql+psycopg://x:x@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    client = TestClient(create_app(session_factory=sessionmaker(bind=broken)))
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database_unavailable"
