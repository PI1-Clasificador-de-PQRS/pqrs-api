from enum import StrEnum


class ErrorCode(StrEnum):
    """Códigos de error estables del contrato público."""

    MISSING_CREDENTIALS = "missing_credentials"
    INVALID_CREDENTIALS = "invalid_credentials"
    SESSION_EXPIRED = "session_expired"
    SESSION_REQUIRED = "session_required"
    CSRF_REQUIRED = "csrf_required"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    BATCH_TOO_LARGE = "batch_too_large"
    INVALID_REQUEST = "invalid_request"
    TOO_MANY_ATTEMPTS = "too_many_attempts"
    RATE_LIMITED = "rate_limited"
    NOT_IMPLEMENTED = "not_implemented"
    LLM_UNAVAILABLE = "llm_unavailable"
    CLASSIFIER_UNAVAILABLE = "classifier_unavailable"
    DATABASE_UNAVAILABLE = "database_unavailable"
    INTERNAL_ERROR = "internal_error"


class DomainError(Exception):
    """Error de negocio con código estable y mensaje en español."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
