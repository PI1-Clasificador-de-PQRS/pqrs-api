"""Siembra de datos de prueba directamente con los repositorios."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.domain.entities import ApiKey, AuthSession, Classification, Organization, User
from app.infrastructure.repositories.uow import SqlUnitOfWork

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


@dataclass
class SeededOrg:
    org: Organization
    user: User
    api_key: ApiKey
    session: AuthSession
    classification: Classification


def seed_org(
    db: Session,
    name: str,
    *,
    email: str | None = None,
    password_hash: str = "hash-not-used",
    batch_id: UUID | None = None,
) -> SeededOrg:
    uow = SqlUnitOfWork(db)
    org = uow.organizations.add(name=name, created_at=NOW)
    user = uow.users.add(
        org.id,
        email=email or f"{uuid4().hex[:8]}@example.com",
        name=f"Usuario {name}",
        password_hash=password_hash,
        created_at=NOW,
    )
    api_key = uow.api_keys.add(
        org.id,
        name=f"Clave {name}",
        prefix="pqrs_abcdefg",
        key_hash=uuid4().hex + uuid4().hex,
        created_at=NOW,
    )
    session = uow.sessions.add(
        org.id, user_id=user.id, token_hash=uuid4().hex + uuid4().hex, created_at=NOW
    )
    classification = uow.classifications.add(
        org.id,
        text_anonymized=f"Texto anonimizado de {name}",
        category="Reclamo",
        engine="classifier",
        model_version="v1",
        prompt_version=None,
        source="api",
        batch_id=batch_id,
        external_ref=None,
        latency_ms=12,
        user_id=None,
        api_key_id=api_key.id,
        created_at=NOW,
    )
    uow.commit()
    return SeededOrg(org, user, api_key, session, classification)
