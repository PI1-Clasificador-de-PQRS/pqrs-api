import hmac
import ipaddress
from collections.abc import Iterator
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Header, Request, Security
from fastapi.security import APIKeyCookie, APIKeyHeader
from pqrs_classifier.preprocessing import anonymize
from sqlalchemy.orm import Session

from app.api.client_ip import resolve_client_ip
from app.api.errors import ApiError
from app.application.auth import AuthService
from app.application.classification import ClassificationService
from app.application.ports import RequestRateLimiter, UnitOfWork
from app.application.reporting import HistoryService, MetricsService
from app.domain.errors import DomainError, ErrorCode
from app.domain.principal import Principal
from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.engines.registry import EngineRegistry
from app.infrastructure.repositories.uow import SqlUnitOfWork

CSRF_HEADER = "X-PQRS-CSRF"
PROXY_SECRET_HEADER = "X-PQRS-Proxy-Secret"
PROXY_CLIENT_IP_HEADER = "X-PQRS-Client-IP"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Solo documentan el esquema de seguridad en el OpenAPI; la lectura real de la cookie usa
# ``app.state.settings``. Ambos salen de la misma configuración.
_session_cookie_scheme = APIKeyCookie(
    name=get_settings().session_cookie_name,
    scheme_name="SessionCookie",
    description="Sesión de usuario. El dashboard (BFF) reenvía la cookie a la API.",
    auto_error=False,
)


_api_key_scheme = APIKeyHeader(
    name="X-API-Key",
    scheme_name="ApiKeyAuth",
    description="Clave de un sistema externo (`pqrs_...`). Solo permite clasificar.",
    auto_error=False,
)


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_registry(request: Request) -> EngineRegistry:
    registry: EngineRegistry = request.app.state.registry
    return registry


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
        session_max=timedelta(hours=settings.session_max_hours),
    )


def get_classification_service(
    request: Request,
    uow: Annotated[UnitOfWork, Depends(get_uow)],
    registry: Annotated[EngineRegistry, Depends(get_registry)],
) -> ClassificationService:
    return ClassificationService(uow, registry, request.app.state.clock, anonymize)


def get_history_service(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> HistoryService:
    return HistoryService(uow)


def get_metrics_service(uow: Annotated[UnitOfWork, Depends(get_uow)]) -> MetricsService:
    return MetricsService(uow)


def proxy_secret_is_valid(settings: Settings, given: str | None) -> bool:
    """¿Llegó el secreto compartido con el proxy del dashboard? Comparación en tiempo constante."""
    secret = settings.proxy_shared_secret
    if secret is None or not secret.get_secret_value() or given is None:
        return False
    return hmac.compare_digest(given.encode(), secret.get_secret_value().encode())


def enforce_proxy(settings: Settings, given: str | None) -> None:
    """Exige que la petición venga del proxy del dashboard (403 ``proxy_required``).

    Solo ``ENVIRONMENT=development`` sin ``PROXY_SHARED_SECRET`` lo deja pasar. Con un secreto
    definido se exige siempre, también en desarrollo.
    """
    if settings.proxy_shared_secret is None and settings.is_development:
        return
    if not proxy_secret_is_valid(settings, given):
        raise ApiError(ErrorCode.PROXY_REQUIRED)


ProxySecretHeader = Annotated[
    str | None,
    Header(
        alias=PROXY_SECRET_HEADER,
        description=(
            "Secreto compartido con el proxy del dashboard (`PROXY_SHARED_SECRET`). Es obligatorio "
            "en el login, el logout y toda petición autenticada con la cookie de sesión; sin un "
            "secreto válido la respuesta es 403 `proxy_required`. No se exige con `X-API-Key`."
        ),
    ),
]
ProxyClientIpHeader = Annotated[
    str | None,
    Header(
        alias=PROXY_CLIENT_IP_HEADER,
        description=(
            "IP del navegador que el proxy del dashboard reenvía. Solo se usa si "
            f"`{PROXY_SECRET_HEADER}` es válido; si no, la IP se deduce de `X-Forwarded-For` "
            "según `TRUSTED_PROXY_HOPS`. Alimenta el límite de intentos de login."
        ),
    ),
]


def require_proxy(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    proxy_secret: ProxySecretHeader = None,
    _client_ip: ProxyClientIpHeader = None,
) -> None:
    """Dependencia de las rutas que siempre exigen el proxy (login y logout)."""
    enforce_proxy(settings, proxy_secret)


def client_ip(request: Request, settings: Settings) -> str | None:
    """IP del cliente para el límite de intentos de login.

    Con un secreto de proxy válido y una ``X-PQRS-Client-IP`` que sea una IP, se usa esa. En
    cualquier otro caso (sin secreto, secreto incorrecto o IP inválida) se usa la lógica de
    ``TRUSTED_PROXY_HOPS``: quien llama directo a la API no puede elegir su IP.
    """
    if proxy_secret_is_valid(settings, request.headers.get(PROXY_SECRET_HEADER.lower())):
        claimed = request.headers.get(PROXY_CLIENT_IP_HEADER.lower())
        if claimed:
            try:
                return str(ipaddress.ip_address(claimed.strip()))
            except ValueError:
                pass
    # Un proxy puede agregar su entrada en una línea nueva en vez de añadirla a la existente:
    # se unen todas antes de contar desde la derecha.
    forwarded_for = ",".join(request.headers.getlist("x-forwarded-for")) or None
    peer = request.client.host if request.client else None
    return resolve_client_ip(forwarded_for, peer, settings.trusted_proxy_hops)


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
    # Antes de tocar la sesión: una cookie que no llega por el proxy no se acepta.
    enforce_proxy(settings, request.headers.get(PROXY_SECRET_HEADER.lower()))
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
    _proxy_secret: ProxySecretHeader = None,
    _proxy_client_ip: ProxyClientIpHeader = None,
) -> Principal:
    """Exige una sesión de usuario (cookie). Una API key no sirve aquí."""
    principal = _principal_from_cookie(request, settings, service, csrf)
    if principal is not None:
        return principal
    if request.headers.get("x-api-key"):
        raise ApiError(ErrorCode.SESSION_REQUIRED)
    raise ApiError(ErrorCode.MISSING_CREDENTIALS)


def require_session_or_key(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    service: Annotated[AuthService, Depends(get_auth_service)],
    csrf: CsrfHeader = None,
    _cookie: Annotated[str | None, Security(_session_cookie_scheme)] = None,
    _api_key: Annotated[str | None, Security(_api_key_scheme)] = None,
    _proxy_secret: ProxySecretHeader = None,
    _proxy_client_ip: ProxyClientIpHeader = None,
) -> Principal:
    """Acepta una sesión (cookie) o una API key. Si llegan ambas, gana la API key.

    La cabecera CSRF y el secreto del proxy solo se exigen en la rama de cookie.
    """
    raw_key = request.headers.get("x-api-key")
    if raw_key:
        try:
            return service.resolve_api_key(raw_key)
        except DomainError as exc:
            raise _domain_to_api_error(exc) from exc
    principal = _principal_from_cookie(request, settings, service, csrf)
    if principal is not None:
        return principal
    raise ApiError(ErrorCode.MISSING_CREDENTIALS)


def get_request_limiter(request: Request) -> RequestRateLimiter:
    limiter: RequestRateLimiter = request.app.state.request_limiter
    return limiter


def require_session_or_key_limited(
    principal: Annotated[Principal, Depends(require_session_or_key)],
    limiter: Annotated[RequestRateLimiter, Depends(get_request_limiter)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Principal:
    """Autentica (sesión o API key) y aplica el límite de solicitudes por minuto.

    El límite es por API key o por usuario, **nunca por IP**. Cuenta toda solicitud autenticada.
    """
    if principal.api_key_id is not None:
        key, limit = f"key:{principal.api_key_id}", settings.rate_limit_key_per_minute
    else:
        key, limit = f"user:{principal.user_id}", settings.rate_limit_user_per_minute
    retry_after = limiter.hit(key, limit)
    if retry_after is not None:
        raise ApiError(ErrorCode.RATE_LIMITED, headers={"Retry-After": str(retry_after)})
    return principal
