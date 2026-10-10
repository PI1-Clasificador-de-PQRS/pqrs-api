import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.api.errors import DEFAULT_MESSAGES, error_body, install_error_handlers
from app.api.middleware import BodyLimitMiddleware, RequestLogMiddleware
from app.api.routers import auth, classifications, classify, health, me, metrics, models
from app.application.ports import Clock, PasswordHasher
from app.domain.errors import ErrorCode
from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.db.session import get_sessionmaker
from app.infrastructure.engines.registry import EngineRegistry
from app.infrastructure.logging import configure_logging
from app.infrastructure.ner import check_ner_available
from app.infrastructure.security.clock import SystemClock
from app.infrastructure.security.passwords import Argon2PasswordHasher
from app.infrastructure.security.rate_limit import InMemoryLoginRateLimiter
from app.infrastructure.security.request_rate_limit import InMemoryRequestRateLimiter

logger = logging.getLogger("pqrs.api")

API_VERSION = "0.1.0"
API_PREFIX = "/api/v1"

OPENAPI_TAGS = [
    {"name": "auth", "description": "Inicio y cierre de sesión, y datos de la sesión."},
    {"name": "models", "description": "Motores de clasificación disponibles."},
    {"name": "classify", "description": "Clasificación de PQRS (sesión o API key)."},
    {"name": "history", "description": "Historial de clasificaciones (solo sesión)."},
    {"name": "metrics", "description": "Métricas agregadas (solo sesión)."},
    {"name": "ops", "description": "Sondas de salud para el orquestador."},
]

API_DESCRIPTION = """\
API del MVP del Clasificador automático de PQRS (Petición, Reclamo, Denuncia, Solicitud de \
información).

## Credenciales

* **Sesión (personas):** cookie `HttpOnly` que fija `POST /auth/login`. El navegador solo habla \
con el dashboard (Next.js), que actúa como proxy y reenvía la cookie a esta API.
* **API key (sistemas externos):** cabecera `X-API-Key`. Una API key **solo puede clasificar** \
(`/models`, `/classify`, `/classify/batch`); historial, métricas y `/me` exigen sesión.
* **CSRF:** las peticiones POST, PUT, PATCH y DELETE autenticadas con cookie deben llevar la \
cabecera `X-PQRS-CSRF: 1` (la añade el proxy del dashboard). El login y las peticiones con \
API key están exentos.
* **Proxy de confianza:** la API es pública, así que el login, el logout y toda petición \
autenticada con la cookie exigen además `X-PQRS-Proxy-Secret` (el secreto compartido con el \
dashboard); sin un secreto válido responden 403 `proxy_required`. El proxy envía también la IP \
del navegador en `X-PQRS-Client-IP` (solo se usa con un secreto válido). Las peticiones con \
`X-API-Key` no pasan por el proxy, y el proxy **no** debe reenviar `X-API-Key` desde el navegador.

## Límites

* **Tamaño del cuerpo:** `MAX_BODY_BYTES` (1 MiB por defecto); más responde 413 `payload_too_large`.
* **Solicitudes** a `/models`, `/classify` y `/classify/batch`: por minuto y por API key o por \
usuario (nunca por IP); al superarlo, 429 `rate_limited` con `Retry-After`.
* **Login:** intentos fallidos limitados por (correo, IP), por IP y por correo; 429 \
`too_many_attempts` con `Retry-After`.

Toda credencial se resuelve en una organización; un recurso de otra organización responde 404.

## Errores

Todos los errores tienen la forma `{"detail": {"code": "<código_estable>", "message": "<texto>"}}`.

| HTTP | Códigos |
|---|---|
| 401 | `missing_credentials`, `invalid_credentials`, `session_expired`, `session_required` |
| 403 | `csrf_required`, `proxy_required` |
| 404 | `not_found` |
| 413 | `batch_too_large`, `payload_too_large` |
| 422 | `invalid_request` (en un lote, por elemento: `invalid_text`, `invalid_external_ref`) |
| 429 | `too_many_attempts`, `rate_limited` |
| 503 | `llm_unavailable`, `classifier_unavailable` |
"""


def _use_single_error_format(app: FastAPI) -> None:
    """Sustituye el 422 por defecto de FastAPI (``HTTPValidationError``) por el formato único.

    En ejecución las validaciones ya responden ``{"detail": {"code": "invalid_request", ...}}``;
    esto hace que el OpenAPI diga lo mismo.
    """
    original = app.openapi

    def openapi() -> dict[str, Any]:
        schema = original()
        invalid_request = {
            "description": "Códigos posibles: `invalid_request`",
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/ErrorResponse"},
                    "examples": {
                        "invalid_request": {
                            "value": error_body(
                                ErrorCode.INVALID_REQUEST,
                                DEFAULT_MESSAGES[ErrorCode.INVALID_REQUEST],
                            )
                        }
                    },
                }
            },
        }
        for operations in schema.get("paths", {}).values():
            for operation in operations.values():
                response = operation.get("responses", {}).get("422")
                if response is not None and "HTTPValidationError" in json.dumps(response):
                    operation["responses"]["422"] = invalid_request
        for name in ("HTTPValidationError", "ValidationError"):
            schema.get("components", {}).get("schemas", {}).pop(name, None)
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def create_app(
    settings: Settings | None = None,
    session_factory: sessionmaker[Session] | None = None,
    clock: Clock | None = None,
    hasher: PasswordHasher | None = None,
    registry: EngineRegistry | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    clock = clock or SystemClock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # El NER va primero: solo el primer ``anonymize`` del proceso avisa si falta el modelo.
        if not check_ner_available():
            message = "El NER de nombres (spaCy es_core_news_sm) no está disponible."
            if not settings.is_development:
                raise RuntimeError(message)
            logger.warning("ner_unavailable_names_not_anonymized")
        app.state.registry.load()  # no hace nada si ya se inyectaron los motores
        yield

    app = FastAPI(
        lifespan=lifespan,
        title="pqrs-api",
        version=API_VERSION,
        summary="API del Clasificador automático de PQRS",
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
    )
    app.state.settings = settings
    app.state.registry = registry or EngineRegistry(settings, clock)
    app.state.session_factory = session_factory or get_sessionmaker()
    app.state.clock = clock
    app.state.hasher = hasher or Argon2PasswordHasher()
    app.state.rate_limiter = InMemoryLoginRateLimiter(
        max_attempts=settings.login_max_attempts,
        max_attempts_per_ip=settings.login_max_attempts_per_ip,
        max_attempts_per_email=settings.login_max_attempts_per_email,
        window=timedelta(minutes=settings.login_window_minutes),
        max_keys=settings.login_limiter_max_keys,
        clock=clock,
    )

    app.state.request_limiter = InMemoryRequestRateLimiter(
        window=timedelta(minutes=1), max_keys=settings.login_limiter_max_keys, clock=clock
    )

    _use_single_error_format(app)
    install_error_handlers(app)
    # El último middleware agregado es el más externo: el de log ve también los 413.
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(RequestLogMiddleware)
    app.include_router(health.router)
    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(me.router, prefix=API_PREFIX)
    app.include_router(models.router, prefix=API_PREFIX)
    app.include_router(classify.router, prefix=API_PREFIX)
    app.include_router(classifications.router, prefix=API_PREFIX)
    app.include_router(metrics.router, prefix=API_PREFIX)
    return app


def get_app() -> FastAPI:
    """Fábrica para uvicorn (`--factory`): configura el logging y crea la aplicación."""
    settings = get_settings()
    settings.require_runtime_config()  # sin PROXY_SHARED_SECRET (fuera de desarrollo) no arranca
    configure_logging(settings.log_level)
    return create_app(settings)
