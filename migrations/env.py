from alembic import context
from sqlalchemy import create_engine, pool

from app.infrastructure.config.settings import get_settings
from app.infrastructure.db import models  # noqa: F401  (registra las tablas en el metadata)
from app.infrastructure.db.base import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    # Las pruebas pasan la URL por config.attributes para no tocar el entorno.
    override = config.attributes.get("database_url")
    return str(override) if override else get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
