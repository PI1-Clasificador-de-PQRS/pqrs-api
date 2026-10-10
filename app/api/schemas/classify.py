from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.api.schemas.common import ApiSchema
from app.domain.categories import Category, Engine
from app.domain.errors import ErrorCode

MODEL_FIELD_DESCRIPTION = (
    "Motor a usar. Si se omite, la API elige el motor predeterminado disponible."
)


class ClassifyRequest(ApiSchema):
    text: str = Field(min_length=1, max_length=5000, description="Texto de la PQRS (1–5000).")
    model: Engine | None = Field(default=None, description=MODEL_FIELD_DESCRIPTION)
    external_ref: str | None = Field(
        default=None,
        max_length=100,
        description="Referencia del sistema que llama (por ejemplo, su número de radicado).",
    )


class ClassifyResponse(ApiSchema):
    id: UUID
    category: Category
    model: Engine
    model_version: str
    timestamp: datetime


class BatchItem(ApiSchema):
    text: str = Field(
        description=(
            "Texto de la PQRS (1–5000). No se valida aquí: un elemento inválido recibe su "
            "propio `error` en la respuesta sin detener el resto del lote."
        )
    )
    external_ref: str | None = Field(default=None, description="Referencia externa (máx. 100).")


class BatchRequest(ApiSchema):
    items: list[BatchItem] = Field(
        min_length=1,
        description=("Entre 1 y 50 elementos. Más de 50 responde 413 `batch_too_large` (no 422)."),
    )
    model: Engine | None = Field(default=None, description=MODEL_FIELD_DESCRIPTION)
    batch_id: UUID | None = Field(
        default=None, description="Id del lote; si se omite, la API genera uno."
    )


class BatchItemError(ApiSchema):
    code: ErrorCode
    message: str


class BatchItemResult(ApiSchema):
    index: int = Field(description="Posición del elemento en `items` (desde 0).")
    id: UUID | None = None
    category: Category | None = None
    model_version: str | None = None
    error: BatchItemError | None = Field(
        default=None, description="Presente solo si este elemento falló."
    )


class BatchResponse(ApiSchema):
    batch_id: UUID
    model: Engine
    results: list[BatchItemResult]
