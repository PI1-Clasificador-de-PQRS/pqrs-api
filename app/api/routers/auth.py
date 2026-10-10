from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import (
    client_ip,
    get_auth_service,
    get_settings_dep,
    require_session,
)
from app.api.errors import ApiError, error_responses
from app.api.schemas.auth import LoginRequest, MeResponse, to_me_response
from app.application.auth import AuthService
from app.domain.errors import DomainError, ErrorCode
from app.domain.principal import Principal
from app.infrastructure.config.settings import Settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/login",
    summary="Iniciar sesión",
    response_model=MeResponse,
    responses=error_responses(
        ErrorCode.INVALID_CREDENTIALS, ErrorCode.TOO_MANY_ATTEMPTS, ErrorCode.INVALID_REQUEST
    ),
)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> MeResponse:
    """Valida las credenciales y fija la cookie de sesión (`HttpOnly`, `SameSite=Lax`).

    Cualquier fallo (contraseña incorrecta, usuario inexistente o inactivo, organización
    inactiva) responde igual: 401 `invalid_credentials`. No exige `X-PQRS-CSRF`.
    """
    try:
        result = service.login(
            email=body.email, password=body.password, ip=client_ip(request, settings)
        )
    except DomainError as exc:
        headers = None
        if exc.code == ErrorCode.TOO_MANY_ATTEMPTS:
            headers = {"Retry-After": str(settings.login_window_minutes * 60)}
        raise ApiError(exc.code, exc.message, headers=headers) from exc
    response.set_cookie(
        settings.session_cookie_name,
        result.token,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return to_me_response(result.me)


@router.post(
    "/logout",
    summary="Cerrar sesión",
    status_code=204,
    responses=error_responses(
        ErrorCode.MISSING_CREDENTIALS,
        ErrorCode.SESSION_EXPIRED,
        ErrorCode.SESSION_REQUIRED,
        ErrorCode.CSRF_REQUIRED,
    ),
)
def logout(
    request: Request,
    principal: Annotated[Principal, Depends(require_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    """Borra la sesión del servidor y la cookie."""
    token = request.cookies.get(settings.session_cookie_name, "")
    service.logout(principal.org_id, token)
    response = Response(status_code=204)
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
    )
    return response
