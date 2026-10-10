from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.config.settings import Settings
from app.infrastructure.security.passwords import Argon2PasswordHasher
from app.main import create_app
from tests.db_utils import TEST_DATABASE_URL, create_database, migrate
from tests.fakes import FakeClock


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
        login_max_attempts=5,
        login_window_minutes=15,
        trust_forwarded_for=False,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def app(
    settings: Settings,
    session_factory: sessionmaker[Session],
    clock: FakeClock,
    hasher: Argon2PasswordHasher,
) -> FastAPI:
    return create_app(settings, session_factory, clock=clock, hasher=hasher)


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # https para que la cookie Secure se reenvíe entre peticiones.
    return TestClient(app, base_url="https://testserver")
