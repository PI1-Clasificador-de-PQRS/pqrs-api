from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.errors import ApiError, error_responses
from app.api.schemas.common import StatusResponse
from app.domain.errors import ErrorCode

router = APIRouter(tags=["ops"])


@router.get("/health", summary="Proceso vivo", response_model=StatusResponse)
def health() -> StatusResponse:
    """Indica que el proceso responde. No toca la base de datos."""
    return StatusResponse(status="ok")


@router.get(
    "/ready",
    summary="Listo para recibir tráfico",
    response_model=StatusResponse,
    responses=error_responses(ErrorCode.DATABASE_UNAVAILABLE),
)
def ready(db: Session = Depends(get_db)) -> StatusResponse:
    """Comprueba que la base de datos es accesible.

    En la parte 2 también exigirá que el modelo local esté cargado.
    """
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        raise ApiError(ErrorCode.DATABASE_UNAVAILABLE) from exc
    return StatusResponse(status="ready")
