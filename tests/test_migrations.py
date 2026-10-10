import uuid

from sqlalchemy import create_engine, inspect

from tests.db_utils import TEST_DATABASE_URL, create_database, downgrade, drop_database, migrate

EXPECTED_TABLES = {"organizations", "users", "sessions", "api_keys", "classifications"}


def test_upgrade_head_and_downgrade_base_on_clean_database() -> None:
    from sqlalchemy.engine import make_url

    url = make_url(TEST_DATABASE_URL).set(database=f"pqrs_mig_{uuid.uuid4().hex[:12]}")
    database_url = url.render_as_string(hide_password=False)
    create_database(database_url)
    try:
        migrate(database_url)
        engine = create_engine(database_url)
        assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES
        engine.dispose()

        downgrade(database_url)
        engine = create_engine(database_url)
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
        engine.dispose()

        # Y vuelve a subir sin problemas.
        migrate(database_url)
    finally:
        drop_database(database_url)
