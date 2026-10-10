from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_session
from app.api.errors import ApiError, error_responses
from app.api.schemas.classifications import ClassificationOut, ClassificationPage
from app.domain.categories import Category, Engine, Source
from app.domain.errors import ErrorCode
from app.domain.principal import Principal

router = APIRouter(tags=["history"])

_SESSION_ERRORS = (
    ErrorCode.MISSING_CREDENTIALS,
    ErrorCode.INVALID_CREDENTIALS,
    ErrorCode.SESSION_EXPIRED,
    ErrorCode.SESSION_REQUIRED,
)


@router.get(
    "/classifications",
    summary="Historial de clasificaciones",
    response_model=ClassificationPage,
    responses=error_responses(
        *_SESSION_ERRORS, ErrorCode.INVALID_REQUEST, ErrorCode.NOT_IMPLEMENTED
    ),
)
def list_classifications(
    principal: Annotated[Principal, Depends(require_session)],
    date_from: Annotated[
        date | None, Query(description="Desde esta fecha (UTC), inclusive. Formato YYYY-MM-DD.")
    ] = None,
    date_to: Annotated[
        date | None, Query(description="Hasta esta fecha (UTC), inclusive. Formato YYYY-MM-DD.")
    ] = None,
    category: Category | None = None,
    model: Engine | None = None,
    source: Source | None = None,
    batch_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ClassificationPage:
    """Historial paginado de la organización de la sesión, del más reciente al más antiguo.

    **Contrato v0:** responde 501 hasta la parte 2. Requiere sesión (una API key no sirve).
    """
    raise ApiError(ErrorCode.NOT_IMPLEMENTED)


@router.get(
    "/classifications/{classification_id}",
    summary="Detalle de una clasificación",
    response_model=ClassificationOut,
    responses=error_responses(
        *_SESSION_ERRORS, ErrorCode.NOT_FOUND, ErrorCode.INVALID_REQUEST, ErrorCode.NOT_IMPLEMENTED
    ),
)
def get_classification(
    classification_id: UUID,
    principal: Annotated[Principal, Depends(require_session)],
) -> ClassificationOut:
    """Una clasificación de la organización de la sesión; la de otra organización da 404.

    **Contrato v0:** responde 501 hasta la parte 2.
    """
    raise ApiError(ErrorCode.NOT_IMPLEMENTED)
