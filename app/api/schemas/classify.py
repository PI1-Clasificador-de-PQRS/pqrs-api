from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import Field, StringConstraints

from app.api.schemas.common import ApiSchema
from app.domain.categories import Category, Engine
from app.domain.errors import ErrorCode

MODEL_FIELD_DESCRIPTION = (
    "Motor a usar. Si se omite, la API elige el motor predeterminado disponible."
)


class ClassifyRequest(ApiSchema):
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=5000)
    ] = Field(
        description=(
            "Texto de la PQRS: de 1 a 5.000 caracteres, contados tras quitar los espacios "
            "del inicio y del final."
        )
    )
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
            "Texto de la PQRS: de 1 a 5.000 caracteres, contados tras quitar los espacios del "
            "inicio y del final. El esquema no lo restringe a propósito: un texto vacío o de más "
            "de 5.000 caracteres no rechaza el lote con 422; ese elemento devuelve su propio "
            "`error` (`invalid_text`) en `results` y el resto se clasifica con normalidad."
        )
    )
    external_ref: str | None = Field(
        default=None,
        description=(
            "Referencia del sistema que llama: máximo 100 caracteres. Si lo excede, ese "
            "elemento devuelve su propio `error` (`invalid_external_ref`) y el resto sigue."
        ),
    )


class BatchRequest(ApiSchema):
    items: list[BatchItem] = Field(
        min_length=1,
        description=(
            "Entre 1 y 50 elementos. Una lista vacía responde 422 `invalid_request`; más de 50 "
            "elementos responde 413 `batch_too_large` (no 422) y no se clasifica ninguno."
        ),
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
        default=None,
        description="Presente solo si este elemento falló; no afecta a los demás.",
    )


class BatchResponse(ApiSchema):
    batch_id: UUID
    model: Engine
    results: list[BatchItemResult]
