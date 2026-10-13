from datetime import date
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_metrics_service, get_registry, get_timezone, require_session
from app.api.errors import error_responses
from app.api.routers.models import to_model_info
from app.api.schemas.metrics import EvaluationSummary, Granularity, MetricsSummary, TimeseriesPoint
from app.application.reporting import MetricsService
from app.domain.categories import Category, Engine, Source
from app.domain.errors import ErrorCode
from app.domain.principal import Principal
from app.infrastructure.engines.registry import EngineRegistry

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
        ErrorCode.PROXY_REQUIRED,
        ErrorCode.INVALID_REQUEST,
    ),
)
def metrics_summary(
    principal: Annotated[Principal, Depends(require_session)],
    service: Annotated[MetricsService, Depends(get_metrics_service)],
    registry: Annotated[EngineRegistry, Depends(get_registry)],
    tz: Annotated[ZoneInfo, Depends(get_timezone)],
    date_from: Annotated[
        date | None, Query(description="Desde esta fecha (en `tz`), inclusive. Formato YYYY-MM-DD.")
    ] = None,
    date_to: Annotated[
        date | None, Query(description="Hasta esta fecha (en `tz`), inclusive. Formato YYYY-MM-DD.")
    ] = None,
    granularity: Granularity = Granularity.DAY,
) -> MetricsSummary:
    """Totales por categoría, motor y origen, serie temporal y F1 macro de cada motor.

    Requiere sesión (una API key no sirve). `by_category`, `by_model` y `by_source` traen
    **todas** las claves posibles, también las que valen 0. `total` coincide con el `total` de
    `GET /classifications` con las mismas fechas y la misma `tz`.

    Los días de `date_from` y `date_to` y los periodos de la serie se interpretan en la zona `tz`
    (por defecto, UTC; el dashboard envía `America/Bogota`); `timezone` devuelve la zona aplicada.
    `timeseries` cuenta por día local (`day`) o por semana ISO que empieza el lunes local (`week`);
    `period` es la fecha local de inicio. No tiene huecos: los periodos sin clasificaciones valen 0.
    Si falta `date_from` o `date_to`, la serie empieza o termina en el día local de la primera o
    última clasificación; sin ninguna fecha ni datos queda vacía. Un rango de más de 400 periodos
    responde 422 `invalid_request`.
    """
    data = service.summary(
        principal.org_id,
        date_from=date_from,
        date_to=date_to,
        granularity=granularity.value,
        tz=tz,
    )
    evaluation = {s.id: to_model_info(s).evaluation for s in registry.status()}
    return MetricsSummary(
        total=data.total,
        by_category={Category(k): v for k, v in data.by_category.items()},
        by_model={Engine(k): v for k, v in data.by_engine.items()},
        by_source={Source(k): v for k, v in data.by_source.items()},
        timeseries=[TimeseriesPoint(period=p, count=n) for p, n in data.timeseries],
        evaluation=EvaluationSummary(llm=evaluation["llm"], classifier=evaluation["classifier"]),
        timezone=data.timezone,
    )
