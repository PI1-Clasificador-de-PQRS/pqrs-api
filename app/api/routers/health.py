from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_registry
from app.api.errors import ApiError, error_responses
from app.api.schemas.common import StatusResponse
from app.domain.errors import ErrorCode
from app.infrastructure.engines.registry import EngineRegistry

router = APIRouter(tags=["ops"])


@router.get("/health", summary="Proceso vivo", response_model=StatusResponse)
def health() -> StatusResponse:
    """Indica que el proceso responde. No toca la base de datos."""
    return StatusResponse(status="ok")


@router.get(
    "/ready",
    summary="Listo para recibir tráfico",
    response_model=StatusResponse,
    responses=error_responses(ErrorCode.DATABASE_UNAVAILABLE, ErrorCode.CLASSIFIER_UNAVAILABLE),
)
def ready(
    db: Session = Depends(get_db), registry: EngineRegistry = Depends(get_registry)
) -> StatusResponse:
    """Comprueba que la base de datos es accesible y que el modelo local está cargado.

    El LLM no cuenta: es opcional y su disponibilidad se consulta en `GET /models`.
    """
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise ApiError(ErrorCode.DATABASE_UNAVAILABLE) from exc
    if not registry.classifier_loaded:
        raise ApiError(ErrorCode.CLASSIFIER_UNAVAILABLE)
    return StatusResponse(status="ready")
