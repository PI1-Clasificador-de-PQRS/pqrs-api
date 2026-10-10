from datetime import timedelta

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import install_error_handlers
from app.api.routers import auth, health, me
from app.application.ports import Clock, PasswordHasher
from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.db.session import get_sessionmaker
from app.infrastructure.security.clock import SystemClock
from app.infrastructure.security.passwords import Argon2PasswordHasher
from app.infrastructure.security.rate_limit import InMemoryLoginRateLimiter

API_VERSION = "0.1.0"
API_PREFIX = "/api/v1"


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    clock: Clock | None = None,
    hasher: PasswordHasher | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    clock = clock or SystemClock()
    app = FastAPI(
        title="pqrs-api",
        version=API_VERSION,
        summary="API del Clasificador automático de PQRS",
    )
    app.state.settings = settings
    app.state.session_factory = session_factory or get_sessionmaker()
    app.state.clock = clock
    app.state.hasher = hasher or Argon2PasswordHasher()
    app.state.rate_limiter = InMemoryLoginRateLimiter(
        max_attempts=settings.login_max_attempts,
        window=timedelta(minutes=settings.login_window_minutes),
        clock=clock,
    )

    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(me.router, prefix=API_PREFIX)
    return app


def get_app() -> FastAPI:
    return create_app()
