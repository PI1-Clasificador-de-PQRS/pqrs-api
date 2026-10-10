from collections.abc import Iterator

import pytest
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from tests.db_utils import TEST_DATABASE_URL, create_database, migrate


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
