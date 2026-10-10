from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class CredentialType(StrEnum):
    SESSION = "session"
    API_KEY = "api_key"


@dataclass(frozen=True, slots=True)
class Principal:
    """Identidad resuelta a partir de una credencial.

    El ``org_id`` siempre sale de aquí, nunca de la petición.
    """

    org_id: UUID
    credential_type: CredentialType
    user_id: UUID | None = None
    api_key_id: UUID | None = None
