from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_auth_service, require_session
from app.api.errors import ApiError, error_responses
from app.api.schemas.auth import MeResponse, to_me_response
from app.application.auth import AuthService
from app.domain.errors import DomainError, ErrorCode
from app.domain.principal import Principal

router = APIRouter(tags=["auth"])


@router.get(
    "/me",
    summary="Usuario y organización de la sesión",
    response_model=MeResponse,
    responses=error_responses(
        ErrorCode.PROXY_REQUIRED,
        ErrorCode.MISSING_CREDENTIALS,
        ErrorCode.SESSION_EXPIRED,
        ErrorCode.SESSION_REQUIRED,
    ),
)
def me(
    principal: Annotated[Principal, Depends(require_session)],
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> MeResponse:
    """Requiere sesión: una API key responde 401 `session_required`."""
    try:
        return to_me_response(service.get_me(principal))
    except DomainError as exc:
        raise ApiError(exc.code, exc.message, clear_session_cookie=True) from exc
