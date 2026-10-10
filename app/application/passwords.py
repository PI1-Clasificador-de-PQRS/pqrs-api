from app.domain.errors import DomainError, ErrorCode

MIN_PASSWORD_LENGTH = 12


def validate_password_policy(password: str) -> None:
    """Política de contraseñas al crearlas o restablecerlas (no se aplica al iniciar sesión)."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise DomainError(
            ErrorCode.INVALID_REQUEST,
            f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.",
        )
