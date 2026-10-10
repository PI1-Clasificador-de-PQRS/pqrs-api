import logging
from typing import Any, TypeVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas.common import ErrorResponse
from app.domain.errors import DomainError, ErrorCode, ServiceUnavailable

logger = logging.getLogger("pqrs.api")

T = TypeVar("T")

STATUS_BY_CODE: dict[ErrorCode, int] = {
    ErrorCode.MISSING_CREDENTIALS: 401,
    ErrorCode.INVALID_CREDENTIALS: 401,
    ErrorCode.SESSION_EXPIRED: 401,
    ErrorCode.SESSION_REQUIRED: 401,
    ErrorCode.CSRF_REQUIRED: 403,
    ErrorCode.PROXY_REQUIRED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.BATCH_TOO_LARGE: 413,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.INVALID_REQUEST: 422,
    ErrorCode.INVALID_TEXT: 422,
    ErrorCode.INVALID_EXTERNAL_REF: 422,
    ErrorCode.TOO_MANY_ATTEMPTS: 429,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.LLM_UNAVAILABLE: 503,
    ErrorCode.CLASSIFIER_UNAVAILABLE: 503,
    ErrorCode.DATABASE_UNAVAILABLE: 503,
    ErrorCode.INTERNAL_ERROR: 500,
}

DEFAULT_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.MISSING_CREDENTIALS: "Se requieren credenciales: inicia sesión o envía X-API-Key.",
    ErrorCode.INVALID_CREDENTIALS: "Credenciales inválidas.",
    ErrorCode.SESSION_EXPIRED: "La sesión expiró. Inicia sesión de nuevo.",
    ErrorCode.SESSION_REQUIRED: "Este recurso requiere una sesión de usuario, no una API key.",
    ErrorCode.CSRF_REQUIRED: "Falta la cabecera X-PQRS-CSRF: 1 en una petición que modifica datos.",
    ErrorCode.PROXY_REQUIRED: "Esta petición debe llegar a través del proxy del dashboard.",
    ErrorCode.NOT_FOUND: "Recurso no encontrado.",
    ErrorCode.METHOD_NOT_ALLOWED: "Método no permitido para este recurso.",
    ErrorCode.BATCH_TOO_LARGE: "El lote supera el máximo de 50 elementos.",
    ErrorCode.PAYLOAD_TOO_LARGE: "El cuerpo de la petición supera el tamaño máximo permitido.",
    ErrorCode.INVALID_REQUEST: "La solicitud no es válida.",
    ErrorCode.INVALID_TEXT: "El texto debe tener entre 1 y 5000 caracteres.",
    ErrorCode.INVALID_EXTERNAL_REF: "La referencia externa no puede superar 100 caracteres.",
    ErrorCode.TOO_MANY_ATTEMPTS: "Demasiados intentos. Inténtalo de nuevo más tarde.",
    ErrorCode.RATE_LIMITED: "Se superó el límite de solicitudes. Inténtalo de nuevo más tarde.",
    ErrorCode.LLM_UNAVAILABLE: "El motor LLM no está disponible temporalmente.",
    ErrorCode.CLASSIFIER_UNAVAILABLE: "El clasificador local no está disponible.",
    ErrorCode.DATABASE_UNAVAILABLE: "La base de datos no está disponible.",
    ErrorCode.INTERNAL_ERROR: "Error interno del servidor.",
}


class ApiError(Exception):
    """Error HTTP con el formato único ``{"detail": {"code", "message"}}``."""

    def __init__(
        self,
        code: ErrorCode,
        message: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        clear_session_cookie: bool = False,
    ) -> None:
        self.code = code
        self.message = message or DEFAULT_MESSAGES[code]
        self.status_code = STATUS_BY_CODE[code]
        self.headers = headers
        self.clear_session_cookie = clear_session_cookie
        super().__init__(self.message)


def error_body(code: ErrorCode, message: str) -> dict[str, Any]:
    return {"detail": {"code": code.value, "message": message}}


def or_404(obj: T | None, message: str | None = None) -> T:
    """Convierte ``None`` (recurso inexistente o de otra organización) en 404."""
    if obj is None:
        raise ApiError(ErrorCode.NOT_FOUND, message)
    return obj


def error_responses(*codes: ErrorCode) -> dict[int | str, dict[str, Any]]:
    """Declara en el OpenAPI las respuestas de error de una ruta, con un ejemplo por código."""
    by_status: dict[int, list[ErrorCode]] = {}
    for code in codes:
        by_status.setdefault(STATUS_BY_CODE[code], []).append(code)
    responses: dict[int | str, dict[str, Any]] = {}
    for status, status_codes in sorted(by_status.items()):
        responses[status] = {
            "model": ErrorResponse,
            "description": "Códigos posibles: " + ", ".join(f"`{c.value}`" for c in status_codes),
            "content": {
                "application/json": {
                    "examples": {
                        c.value: {"value": error_body(c, DEFAULT_MESSAGES[c])} for c in status_codes
                    }
                }
            },
        }
    return responses


def _json_error(
    code: ErrorCode,
    message: str,
    status_code: int | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        error_body(code, message),
        status_code=status_code or STATUS_BY_CODE[code],
        headers=headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    cookie_name = app.state.settings.session_cookie_name
    cookie_secure = app.state.settings.session_cookie_secure

    async def handle_api_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, ApiError)
        response = _json_error(exc.code, exc.message, exc.status_code, exc.headers)
        if exc.clear_session_cookie:
            response.delete_cookie(
                cookie_name, path="/", httponly=True, secure=cookie_secure, samesite="lax"
            )
        return response

    async def handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, DomainError)
        headers = None
        if isinstance(exc, ServiceUnavailable) and exc.retry_after:
            headers = {"Retry-After": str(exc.retry_after)}
        return _json_error(exc.code, exc.message, headers=headers)

    async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        # Solo se informan los campos con error: nunca el valor recibido, que podría ser
        # el texto original de una PQRS.
        fields: list[str] = []
        for error in exc.errors():
            loc = [str(part) for part in error["loc"] if part not in ("body", "query", "path")]
            field = ".".join(loc)
            if field and field not in fields:
                fields.append(field)
        message = DEFAULT_MESSAGES[ErrorCode.INVALID_REQUEST]
        if fields:
            message += " Revisa los campos: " + ", ".join(fields) + "."
        return _json_error(ErrorCode.INVALID_REQUEST, message)

    async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        if exc.status_code == 404:
            return _json_error(ErrorCode.NOT_FOUND, DEFAULT_MESSAGES[ErrorCode.NOT_FOUND])
        if exc.status_code == 405:
            return _json_error(
                ErrorCode.METHOD_NOT_ALLOWED,
                DEFAULT_MESSAGES[ErrorCode.METHOD_NOT_ALLOWED],
                headers=dict(exc.headers) if exc.headers else None,
            )
        return _json_error(
            ErrorCode.INTERNAL_ERROR, DEFAULT_MESSAGES[ErrorCode.INTERNAL_ERROR], exc.status_code
        )

    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Solo se registra el tipo de excepción: el mensaje o la traza podrían incluir datos
        # de la petición.
        logger.error("unhandled_exception", extra={"exception_type": type(exc).__name__})
        return _json_error(ErrorCode.INTERNAL_ERROR, DEFAULT_MESSAGES[ErrorCode.INTERNAL_ERROR])

    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(DomainError, handle_domain_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected)
