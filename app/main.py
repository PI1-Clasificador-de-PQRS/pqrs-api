from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import install_error_handlers
from app.api.routers import health
from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.db.session import get_sessionmaker

API_VERSION = "0.1.0"
API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="pqrs-api",
        version=API_VERSION,
        summary="API del Clasificador automático de PQRS",
    )
    app.state.settings = settings
    app.state.session_factory = session_factory or get_sessionmaker()

    install_error_handlers(app)
    app.include_router(health.router)
    return app


def get_app() -> FastAPI:
    return create_app()
