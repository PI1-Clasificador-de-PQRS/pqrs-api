from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_session_or_key
from app.api.errors import ApiError, error_responses
from app.api.schemas.models import ModelInfo
from app.domain.errors import ErrorCode
from app.domain.principal import Principal

router = APIRouter(tags=["models"])


@router.get(
    "/models",
    summary="Motores de clasificación disponibles",
    response_model=list[ModelInfo],
    responses=error_responses(
        ErrorCode.MISSING_CREDENTIALS,
        ErrorCode.INVALID_CREDENTIALS,
        ErrorCode.SESSION_EXPIRED,
        ErrorCode.NOT_IMPLEMENTED,
    ),
)
def list_models(
    principal: Annotated[Principal, Depends(require_session_or_key)],
) -> list[ModelInfo]:
    """Lista los motores (`llm`, `classifier`) con su disponibilidad y su F1 macro.

    **Contrato v0:** responde 501 hasta la parte 2. Acepta sesión o API key.
    """
    raise ApiError(ErrorCode.NOT_IMPLEMENTED)
