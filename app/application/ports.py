"""Puertos que la capa de aplicación necesita de la infraestructura.

Regla de aislamiento: todo método de los repositorios de negocio (users, sessions, api_keys,
classifications) recibe ``org_id`` como argumento obligatorio. Las únicas excepciones son las
que resuelven una credencial antes de conocer la organización: ``UserRepository.get_for_login``,
``ApiKeyRepository.get_by_hash`` y ``SessionRepository.get_by_token_hash``.
"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.entities import (
    ApiKey,
    AuthSession,
    Classification,
    Organization,
    User,
)


class OrganizationRepository(Protocol):
    def add(self, *, name: str, created_at: datetime) -> Organization: ...

    def get(self, org_id: UUID) -> Organization | None: ...


class UserRepository(Protocol):
    def add(
        self,
        org_id: UUID,
        *,
        email: str,
        name: str,
        password_hash: str,
        created_at: datetime,
    ) -> User: ...

    def get(self, org_id: UUID, user_id: UUID) -> User | None: ...

    def get_by_email(self, org_id: UUID, email: str) -> User | None: ...

    def list(self, org_id: UUID) -> list[User]: ...

    def set_password_hash(self, org_id: UUID, user_id: UUID, password_hash: str) -> bool: ...

    def deactivate(self, org_id: UUID, user_id: UUID) -> bool: ...

    def get_for_login(self, email: str) -> User | None:
        """Resolución de credenciales: busca por email en todas las organizaciones."""
        ...


class SessionRepository(Protocol):
    def add(
        self, org_id: UUID, *, user_id: UUID, token_hash: str, created_at: datetime
    ) -> AuthSession: ...

    def touch(self, org_id: UUID, session_id: UUID, last_seen_at: datetime) -> None: ...

    def delete(self, org_id: UUID, session_id: UUID) -> bool: ...

    def delete_for_user(self, org_id: UUID, user_id: UUID) -> int: ...

    def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        """Resolución de credenciales: la organización sale de la sesión encontrada."""
        ...


class ApiKeyRepository(Protocol):
    def add(
        self, org_id: UUID, *, name: str, prefix: str, key_hash: str, created_at: datetime
    ) -> ApiKey: ...

    def get(self, org_id: UUID, key_id: UUID) -> ApiKey | None: ...

    def list(self, org_id: UUID) -> list[ApiKey]: ...

    def revoke(self, org_id: UUID, key_id: UUID, revoked_at: datetime) -> bool: ...

    def touch_last_used(self, org_id: UUID, key_id: UUID, used_at: datetime) -> None: ...

    def get_by_hash(self, key_hash: str) -> ApiKey | None:
        """Resolución de credenciales: la organización sale de la clave encontrada."""
        ...


class ClassificationRepository(Protocol):
    def add(
        self,
        org_id: UUID,
        *,
        text_anonymized: str,
        category: str,
        engine: str,
        model_version: str,
        prompt_version: str | None,
        source: str,
        batch_id: UUID | None,
        external_ref: str | None,
        latency_ms: int,
        user_id: UUID | None,
        api_key_id: UUID | None,
        created_at: datetime,
    ) -> Classification: ...

    def get(self, org_id: UUID, classification_id: UUID) -> Classification | None: ...

    def list(
        self, org_id: UUID, *, limit: int = 50, offset: int = 0, batch_id: UUID | None = None
    ) -> list[Classification]: ...


class UnitOfWork(Protocol):
    """Agrupa los repositorios de una transacción. El ``commit`` es siempre explícito."""

    organizations: OrganizationRepository
    users: UserRepository
    sessions: SessionRepository
    api_keys: ApiKeyRepository
    classifications: ClassificationRepository

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
