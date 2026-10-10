from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import require_session_or_key
from app.api.errors import ApiError, error_responses
from app.api.schemas.classify import (
    BatchRequest,
    BatchResponse,
    ClassifyRequest,
    ClassifyResponse,
)
from app.domain.errors import ErrorCode
from app.domain.principal import Principal

router = APIRouter(tags=["classify"])

_AUTH_ERRORS = (
    ErrorCode.MISSING_CREDENTIALS,
    ErrorCode.INVALID_CREDENTIALS,
    ErrorCode.SESSION_EXPIRED,
    ErrorCode.CSRF_REQUIRED,
    ErrorCode.INVALID_REQUEST,
    ErrorCode.RATE_LIMITED,
    ErrorCode.NOT_IMPLEMENTED,
    ErrorCode.LLM_UNAVAILABLE,
    ErrorCode.CLASSIFIER_UNAVAILABLE,
)


@router.post(
    "/classify",
    summary="Clasificar una PQRS",
    response_model=ClassifyResponse,
    responses=error_responses(*_AUTH_ERRORS),
)
def classify(
    body: ClassifyRequest,
    principal: Annotated[Principal, Depends(require_session_or_key)],
) -> ClassifyResponse:
    """Clasifica un texto en una de las 4 categorías. No devuelve confianza.

    **Contrato v0:** responde 501 hasta la parte 2. Acepta sesión o API key. Se guarda solo el
    texto anonimizado, nunca el original.
    """
    raise ApiError(ErrorCode.NOT_IMPLEMENTED)


@router.post(
    "/classify/batch",
    summary="Clasificar un lote de PQRS",
    response_model=BatchResponse,
    responses=error_responses(*_AUTH_ERRORS, ErrorCode.BATCH_TOO_LARGE),
)
def classify_batch(
    body: BatchRequest,
    principal: Annotated[Principal, Depends(require_session_or_key)],
) -> BatchResponse:
    """Clasifica un lote de hasta 50 textos.

    Límites:

    * **Máximo 50 elementos** en `items`; más de 50 responde 413 `batch_too_large` y no se
      clasifica ninguno. Una lista vacía responde 422 `invalid_request`.
    * **Cada texto: de 1 a 5.000 caracteres.** Un elemento que no cumple devuelve su propio
      `error` (`invalid_request`) en `results` sin detener el resto del lote.
    * **`external_ref`: máximo 100 caracteres**, con el mismo error por elemento.

    **Contrato v0:** responde 501 hasta la parte 2. Acepta sesión o API key.
    """
    raise ApiError(ErrorCode.NOT_IMPLEMENTED)
