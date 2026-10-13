from datetime import date
from enum import StrEnum

from pydantic import Field

from app.api.schemas.common import ApiSchema
from app.api.schemas.models import EngineEvaluation
from app.domain.categories import Category, Engine, Source


class Granularity(StrEnum):
    DAY = "day"
    WEEK = "week"


class TimeseriesPoint(ApiSchema):
    period: date = Field(
        description="Fecha local (en `tz`) de inicio del periodo: el día o el lunes de la semana."
    )
    count: int


class EvaluationSummary(ApiSchema):
    llm: EngineEvaluation
    classifier: EngineEvaluation


class MetricsSummary(ApiSchema):
    total: int
    by_category: dict[Category, int] = Field(description="Conteo por categoría.")
    by_model: dict[Engine, int] = Field(description="Conteo por motor.")
    by_source: dict[Source, int] = Field(description="Conteo por origen.")
    timeseries: list[TimeseriesPoint]
    evaluation: EvaluationSummary
    timezone: str = Field(
        description="Zona IANA aplicada a los filtros de fecha y a la serie (eco de `tz`).",
        examples=["America/Bogota"],
    )
