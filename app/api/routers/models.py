from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_registry, require_session_or_key_limited
from app.api.errors import error_responses
from app.api.schemas.models import EngineEvaluation, ModelInfo, UnavailableReason
from app.application.ports import EngineStatus
from app.domain.categories import Engine
from app.domain.errors import ErrorCode
from app.domain.principal import Principal
from app.infrastructure.engines.registry import EngineRegistry

router = APIRouter(tags=["models"])

LABELS = {
    Engine.LLM: "LLM (zero-shot)",
    Engine.CLASSIFIER: "Clasificador local (SBERT + regresión logística)",
}


def to_model_info(status: EngineStatus) -> ModelInfo:
    engine = Engine(status.id)
    return ModelInfo(
        id=engine,
        label=LABELS[engine],
        available=status.available,
        reason=UnavailableReason(status.reason) if status.reason else None,
        retry_after=status.retry_after,
        evaluation=EngineEvaluation(f1_macro=status.f1_macro),
    )


@router.get(
    "/models",
    summary="Motores de clasificación disponibles",
    response_model=list[ModelInfo],
    responses=error_responses(
        ErrorCode.MISSING_CREDENTIALS,
        ErrorCode.INVALID_CREDENTIALS,
        ErrorCode.SESSION_EXPIRED,
        ErrorCode.PROXY_REQUIRED,
        ErrorCode.RATE_LIMITED,
    ),
)
def list_models(
    principal: Annotated[Principal, Depends(require_session_or_key_limited)],
    registry: Annotated[EngineRegistry, Depends(get_registry)],
) -> list[ModelInfo]:
    """Lista los motores (`llm`, `classifier`) con su disponibilidad y su F1 macro.

    Si un motor no está disponible, `reason` dice por qué: `not_configured` (el LLM no tiene
    clave o modelo), `temporarily_unavailable` (circuito de protección abierto; `retry_after`
    son los segundos hasta el siguiente intento) o `not_loaded` (el modelo local no cargó).
    Acepta sesión o API key.
    """
    return [to_model_info(status) for status in registry.status()]
