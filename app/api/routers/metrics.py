from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_session
from app.api.errors import ApiError, error_responses
from app.api.schemas.metrics import Granularity, MetricsSummary
from app.domain.errors import ErrorCode
from app.domain.principal import Principal

router = APIRouter(tags=["metrics"])


@router.get(
    "/metrics/summary",
    summary="Resumen de métricas",
    response_model=MetricsSummary,
    responses=error_responses(
        ErrorCode.MISSING_CREDENTIALS,
        ErrorCode.INVALID_CREDENTIALS,
        ErrorCode.SESSION_EXPIRED,
        ErrorCode.SESSION_REQUIRED,
        ErrorCode.INVALID_REQUEST,
        ErrorCode.NOT_IMPLEMENTED,
    ),
)
def metrics_summary(
    principal: Annotated[Principal, Depends(require_session)],
    date_from: Annotated[
        date | None, Query(description="Desde esta fecha (UTC), inclusive. Formato YYYY-MM-DD.")
    ] = None,
    date_to: Annotated[
        date | None, Query(description="Hasta esta fecha (UTC), inclusive. Formato YYYY-MM-DD.")
    ] = None,
    granularity: Granularity = Granularity.DAY,
) -> MetricsSummary:
    """Totales por categoría, motor y origen, serie temporal y F1 macro de cada motor.

    **Contrato v0:** responde 501 hasta la parte 2. Requiere sesión (una API key no sirve).
    """
    raise ApiError(ErrorCode.NOT_IMPLEMENTED)
