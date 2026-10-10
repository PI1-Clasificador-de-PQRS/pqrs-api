"""Siembra de datos de prueba directamente con los repositorios."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.domain.credentials import generate_api_key, hash_token
from app.domain.entities import ApiKey, AuthSession, Classification, Organization, User
from app.infrastructure.db.models import OrganizationModel
from app.infrastructure.repositories.uow import SqlUnitOfWork
from app.infrastructure.security.passwords import Argon2PasswordHasher

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


def seed_login_user(
    db: Session,
    hasher: Argon2PasswordHasher,
    *,
    email: str = "ana@example.com",
    password: str = "contraseña-segura-1",
    org_name: str = "Org Login",
    org_active: bool = True,
    user_active: bool = True,
) -> tuple[Organization, User]:
    """Organización con un usuario que puede iniciar sesión con ``password``."""
    uow = SqlUnitOfWork(db)
    org = uow.organizations.add(name=org_name, created_at=NOW)
    user = uow.users.add(
        org.id, email=email, name="Ana Pérez", password_hash=hasher.hash(password), created_at=NOW
    )
    if not user_active:
        uow.users.deactivate(org.id, user.id)
    if not org_active:
        db.execute(
            update(OrganizationModel).where(OrganizationModel.id == org.id).values(active=False)
        )
    uow.commit()
    return org, user


def seed_api_key(db: Session, org_id: UUID, name: str = "Integración") -> tuple[str, ApiKey]:
    """Crea una clave y devuelve ``(clave_en_claro, ApiKey)``."""
    raw, prefix = generate_api_key()
    key = SqlUnitOfWork(db).api_keys.add(
        org_id, name=name, prefix=prefix, key_hash=hash_token(raw), created_at=NOW
    )
    db.commit()
    return raw, key
