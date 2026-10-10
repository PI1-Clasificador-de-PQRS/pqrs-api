from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Header, Request, Security
from fastapi.security import APIKeyCookie
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.application.auth import AuthService
from app.application.ports import UnitOfWork
from app.domain.errors import DomainError, ErrorCode
from app.domain.principal import Principal
from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.repositories.uow import SqlUnitOfWork

CSRF_HEADER = "X-PQRS-CSRF"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Solo documentan el esquema de seguridad en el OpenAPI; la lectura real de la cookie usa
# ``app.state.settings``. Ambos salen de la misma configuración.
_session_cookie_scheme = APIKeyCookie(
    name=get_settings().session_cookie_name,
    scheme_name="SessionCookie",
    description="Sesión de usuario. El dashboard (BFF) reenvía la cookie a la API.",
    auto_error=False,
)


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(request: Request) -> Iterator[Session]:
    """Sesión por petición. Los casos de uso hacen ``commit`` explícito.

    No se confía en un commit al salir de la dependencia: FastAPI ejecuta ese código
    después de enviar la respuesta, y el cliente podría anticiparse al commit.
    """
    session: Session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_uow(db: Annotated[Session, Depends(get_db)]) -> UnitOfWork:
    return SqlUnitOfWork(db)


def get_auth_service(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AuthService:
    state = request.app.state
    return AuthService(
        uow,
        hasher=state.hasher,
        limiter=state.rate_limiter,
        clock=state.clock,
        session_idle=timedelta(hours=settings.session_idle_hours),
    )


def client_ip(request: Request, settings: Settings) -> str | None:
    if settings.trust_forwarded_for:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


def _domain_to_api_error(exc: DomainError) -> ApiError:
    return ApiError(
        exc.code,
        exc.message,
        clear_session_cookie=exc.code in (ErrorCode.SESSION_EXPIRED, ErrorCode.INVALID_CREDENTIALS),
    )


def _enforce_csrf(request: Request, csrf: str | None) -> None:
    if request.method in UNSAFE_METHODS and csrf != "1":
        raise ApiError(ErrorCode.CSRF_REQUIRED)


CsrfHeader = Annotated[
    str | None,
    Header(
        alias=CSRF_HEADER,
        description=(
            "Defensa en profundidad contra CSRF. Debe valer `1` en las peticiones POST, PUT, "
            "PATCH y DELETE autenticadas con la cookie de sesión (lo añade el proxy del "
            "dashboard). No se exige con `X-API-Key` ni en el login."
        ),
    ),
]


def _principal_from_cookie(
    request: Request, settings: Settings, service: AuthService, csrf: str | None
) -> Principal | None:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    try:
        principal = service.resolve_session(token)
    except DomainError as exc:
        raise _domain_to_api_error(exc) from exc
    _enforce_csrf(request, csrf)
    return principal


def require_session(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    service: Annotated[AuthService, Depends(get_auth_service)],
    csrf: CsrfHeader = None,
    _cookie: Annotated[str | None, Security(_session_cookie_scheme)] = None,
) -> Principal:
    """Exige una sesión de usuario (cookie). Una API key no sirve aquí."""
    principal = _principal_from_cookie(request, settings, service, csrf)
    if principal is not None:
        return principal
    if request.headers.get("x-api-key"):
        raise ApiError(ErrorCode.SESSION_REQUIRED)
    raise ApiError(ErrorCode.MISSING_CREDENTIALS)
