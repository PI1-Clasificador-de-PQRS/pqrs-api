from enum import StrEnum

from pydantic import Field

from app.api.schemas.common import ApiSchema
from app.domain.categories import Engine


class UnavailableReason(StrEnum):
    NOT_CONFIGURED = "not_configured"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    NOT_LOADED = "not_loaded"


class EngineEvaluation(ApiSchema):
    f1_macro: float = Field(description="F1 macro del motor sobre el conjunto de evaluación.")


class ModelInfo(ApiSchema):
    id: Engine
    label: str = Field(description="Nombre para mostrar en la interfaz.")
    available: bool
    reason: UnavailableReason | None = Field(
        description="Por qué no está disponible; `null` si lo está."
    )
    retry_after: int | None = Field(
        description="Segundos tras los cuales reintentar (solo si `temporarily_unavailable`)."
    )
    evaluation: EngineEvaluation
