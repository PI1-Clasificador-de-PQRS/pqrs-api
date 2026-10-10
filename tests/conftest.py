from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.config.settings import Settings
from app.infrastructure.engines.registry import EngineRegistry
from app.infrastructure.security.passwords import Argon2PasswordHasher
from app.main import create_app
from tests.db_utils import TEST_DATABASE_URL, create_database, migrate
from tests.fakes import FakeClock, RecordingFakeEngine

PROXY_SECRET = "secreto-del-proxy-de-pruebas"
PROXY_HEADERS = {"X-PQRS-Proxy-Secret": PROXY_SECRET}


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """Base de pruebas en PostgreSQL real, recreada en ``head`` una vez por sesión de pytest."""
    create_database(TEST_DATABASE_URL)
    engine = create_engine(TEST_DATABASE_URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    migrate(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def db_connection(db_engine: Engine) -> Iterator[Connection]:
    """Conexión con una transacción externa que se revierte al terminar cada prueba."""
    connection = db_engine.connect()
    transaction = connection.begin()
    yield connection
    transaction.rollback()
    connection.close()


@pytest.fixture
def session_factory(db_connection: Connection) -> sessionmaker[Session]:
    """Fábrica de sesiones unidas a la transacción externa: los ``commit`` solo liberan
    savepoints, así que nada de lo que hace el código bajo prueba sobrevive a la prueba."""
    return sessionmaker(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    yield session
    session.close()


@pytest.fixture(scope="session")
def hasher() -> Argon2PasswordHasher:
    return Argon2PasswordHasher()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        session_cookie_name="pqrs_session",
        session_cookie_secure=True,
        session_idle_hours=8,
        session_max_hours=24,
        login_max_attempts=5,
        login_window_minutes=15,
        trusted_proxy_hops=0,
        proxy_shared_secret=PROXY_SECRET,
        rate_limit_key_per_minute=60,
        rate_limit_user_per_minute=120,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def llm_engine() -> RecordingFakeEngine:
    return RecordingFakeEngine(id="llm", model_version="fake-llm-1", prompt_version="fake-prompt-1")


@pytest.fixture
def classifier_engine() -> RecordingFakeEngine:
    return RecordingFakeEngine(id="classifier", model_version="fake-classifier-1")


@pytest.fixture
def registry(
    settings: Settings,
    clock: FakeClock,
    llm_engine: RecordingFakeEngine,
    classifier_engine: RecordingFakeEngine,
) -> EngineRegistry:
    """Registro con motores falsos: sin red, sin torch."""
    return EngineRegistry(
        settings, clock, llm=llm_engine, classifier=classifier_engine, classifier_f1=0.7776
    )


@pytest.fixture
def app(
    settings: Settings,
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    hasher: Argon2PasswordHasher,
    registry: EngineRegistry,
) -> FastAPI:
    return create_app(settings, session_factory, clock=clock, hasher=hasher, registry=registry)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Cliente que actúa como el proxy del dashboard: envía el secreto compartido.

    Con ``https`` la cookie Secure se reenvía entre peticiones. Las peticiones con API key no
    necesitan el secreto, pero tenerlo no les afecta.
    """
    return TestClient(app, base_url="https://testserver", headers=PROXY_HEADERS)


@pytest.fixture
def direct_client(app: FastAPI) -> TestClient:
    """Cliente que llama directo a la API, sin pasar por el proxy (sin secreto)."""
    return TestClient(app, base_url="https://testserver")
