from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Organization:
    id: UUID
    name: str
    active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class User:
    id: UUID
    org_id: UUID
    email: str
    name: str
    password_hash: str
    active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AuthSession:
    id: UUID
    user_id: UUID
    org_id: UUID
    token_hash: str
    created_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True, slots=True)
class ApiKey:
    id: UUID
    org_id: UUID
    name: str
    prefix: str
    key_hash: str
    active: bool
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class Classification:
    id: UUID
    org_id: UUID
    text_anonymized: str
    category: str
    engine: str
    model_version: str
    prompt_version: str | None
    source: str
    batch_id: UUID | None
    external_ref: str | None
    latency_ms: int
    user_id: UUID | None
    api_key_id: UUID | None
    created_at: datetime
