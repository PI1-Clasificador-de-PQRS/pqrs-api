from pydantic import BaseModel, ConfigDict, Field

from app.domain.errors import ErrorCode


class ApiSchema(BaseModel):
    """Base de los esquemas. ``model_version`` choca con el namespace protegido ``model_``."""

    model_config = ConfigDict(protected_namespaces=())


class ErrorDetail(ApiSchema):
    code: ErrorCode = Field(
        description="Código estable del error; úsalo para la lógica del cliente."
    )
    message: str = Field(description="Mensaje en español, apto para mostrar al usuario.")


class ErrorResponse(ApiSchema):
    """Formato único de error de la API."""

    detail: ErrorDetail


class StatusResponse(ApiSchema):
    status: str
