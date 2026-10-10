from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_history_service, require_session
from app.api.errors import error_responses, or_404
from app.api.schemas.classifications import ClassificationOut, ClassificationPage
from app.application.reporting import HistoryService, build_filters
from app.domain.categories import Category, Engine, Source
from app.domain.entities import Classification
from app.domain.errors import ErrorCode
from app.domain.principal import Principal

router = APIRouter(tags=["history"])

_SESSION_ERRORS = (
    ErrorCode.MISSING_CREDENTIALS,
    ErrorCode.INVALID_CREDENTIALS,
    ErrorCode.SESSION_EXPIRED,
    ErrorCode.SESSION_REQUIRED,
)


def to_out(item: Classification) -> ClassificationOut:
    return ClassificationOut(
        id=item.id,
        created_at=item.created_at,
        text_anonymized=item.text_anonymized,
        category=Category(item.category),
        model=Engine(item.engine),
        model_version=item.model_version,
        source=Source(item.source),
        batch_id=item.batch_id,
        external_ref=item.external_ref,
    )


@router.get(
    "/classifications",
    summary="Historial de clasificaciones",
    response_model=ClassificationPage,
    responses=error_responses(*_SESSION_ERRORS, ErrorCode.INVALID_REQUEST),
)
def list_classifications(
    principal: Annotated[Principal, Depends(require_session)],
    service: Annotated[HistoryService, Depends(get_history_service)],
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

    Los filtros se combinan con AND. `total` es el número de clasificaciones que cumplen los
    filtros (no solo las de la página), igual que el `total` de `GET /metrics/summary` con las
    mismas fechas. Requiere sesión (una API key no sirve).
    """
    filters = build_filters(
        date_from,
        date_to,
        category=category.value if category else None,
        engine=model.value if model else None,
        source=source.value if source else None,
        batch_id=batch_id,
    )
    items, total = service.list(principal.org_id, filters, page=page, page_size=page_size)
    return ClassificationPage(
        items=[to_out(i) for i in items], total=total, page=page, page_size=page_size
    )


@router.get(
    "/classifications/{classification_id}",
    summary="Detalle de una clasificación",
    response_model=ClassificationOut,
    responses=error_responses(*_SESSION_ERRORS, ErrorCode.NOT_FOUND, ErrorCode.INVALID_REQUEST),
)
def get_classification(
    classification_id: UUID,
    principal: Annotated[Principal, Depends(require_session)],
    service: Annotated[HistoryService, Depends(get_history_service)],
) -> ClassificationOut:
    """Una clasificación de la organización de la sesión; la de otra organización da 404."""
    return to_out(or_404(service.get(principal.org_id, classification_id)))
