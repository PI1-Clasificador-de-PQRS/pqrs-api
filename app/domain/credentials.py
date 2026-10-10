"""Generación y hash de credenciales opacas (tokens de sesión y API keys).

Solo se guarda el SHA-256 en hexadecimal; el valor en claro nunca se persiste. Los tokens
tienen 256 bits de entropía, así que un hash rápido es suficiente (no es una contraseña).
"""

import hashlib
import secrets

API_KEY_PREFIX = "pqrs_"
# Caracteres de la clave completa que quedan visibles para identificarla ("pqrs_" + 7).
API_KEY_VISIBLE_LENGTH = 12


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def generate_api_key() -> tuple[str, str]:
    """Devuelve ``(clave_completa, prefijo_visible)``."""
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, key[:API_KEY_VISIBLE_LENGTH]
