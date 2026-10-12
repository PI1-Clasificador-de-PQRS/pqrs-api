"""Puertos que la capa de aplicación necesita de la infraestructura.

Regla de aislamiento: todo método de los repositorios de negocio (users, sessions, api_keys,
classifications) recibe ``org_id`` como argumento obligatorio. Las únicas excepciones son las
que resuelven una credencial antes de conocer la organización: ``UserRepository.get_for_login``,
``ApiKeyRepository.get_by_hash`` y ``SessionRepository.get_by_token_hash``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from pqrs_classifier.engines.base import Prediction

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

    def list(self) -> list[Organization]: ...


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


@dataclass(frozen=True, slots=True)
class ClassificationFilters:
    """Filtros comunes del historial y de las métricas (se aplican igual en ambos)."""

    created_from: datetime | None = None  # inclusive
    created_before: datetime | None = None  # exclusivo
    category: str | None = None
    engine: str | None = None
    source: str | None = None
    batch_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ClassificationCounts:
    """Conteos agregados en SQL; solo traen las claves que tienen filas."""

    total: int
    by_category: dict[str, int]
    by_engine: dict[str, int]
    by_source: dict[str, int]


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

    def search(
        self, org_id: UUID, filters: ClassificationFilters, *, limit: int, offset: int
    ) -> list[Classification]:
        """Del más reciente al más antiguo."""
        ...

    def count(self, org_id: UUID, filters: ClassificationFilters) -> int: ...

    def counts(self, org_id: UUID, filters: ClassificationFilters) -> ClassificationCounts: ...

    def timeseries(
        self, org_id: UUID, filters: ClassificationFilters, *, granularity: str
    ) -> dict[date, int]:
        """Conteo por periodo (``day`` o ``week``, UTC) solo de los periodos con filas."""
        ...

    def bounds(
        self, org_id: UUID, filters: ClassificationFilters
    ) -> tuple[datetime, datetime] | None:
        """Primera y última marca de tiempo de las filas filtradas; ``None`` si no hay."""
        ...


class UnitOfWork(Protocol):
    """Agrupa los repositorios de una transacción. El ``commit`` es siempre explícito."""

    @property
    def organizations(self) -> OrganizationRepository: ...

    @property
    def users(self) -> UserRepository: ...

    @property
    def sessions(self) -> SessionRepository: ...

    @property
    def api_keys(self) -> ApiKeyRepository: ...

    @property
    def classifications(self) -> ClassificationRepository: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class RequestRateLimiter(Protocol):
    def hit(self, key: str, limit: int) -> int | None:
        """Registra una solicitud; ``None`` si pasa, o los segundos hasta poder reintentar."""
        ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool: ...

    def needs_rehash(self, password_hash: str) -> bool: ...

    def verify_dummy(self, password: str) -> None:
        """Verifica contra un hash ficticio para igualar tiempos cuando el usuario no existe."""
        ...


class LoginRateLimiter(Protocol):
    def is_blocked(self, *, email: str, ip: str | None) -> bool: ...

    def record_failure(self, *, email: str, ip: str | None) -> None: ...

    def record_success(self, *, email: str, ip: str | None) -> None: ...


@dataclass(frozen=True, slots=True)
class EngineStatus:
    """Disponibilidad de un motor.

    ``reason``: not_configured, temporarily_unavailable o not_loaded.
    """

    id: str
    available: bool
    reason: str | None
    retry_after: int | None
    f1_macro: float | None


class EngineGateway(Protocol):
    """Acceso a los motores de clasificación (``llm`` y ``classifier``).

    Nunca cambia de motor por su cuenta: ``select`` decide **antes** de clasificar y, si el motor
    elegido falla, ``classify``/``classify_batch`` lanzan ``ServiceUnavailable`` (503).
    """

    def select(self, requested: str | None) -> str:
        """Motor pedido si está disponible; si se omite, ``llm`` o, si no, ``classifier``."""
        ...

    def classify(self, engine_id: str, text: str) -> Prediction: ...

    def classify_batch(self, engine_id: str, texts: Sequence[str]) -> list[Prediction]: ...

    def status(self) -> list[EngineStatus]: ...

    @property
    def classifier_loaded(self) -> bool: ...
