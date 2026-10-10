"""Ayudas comunes de las pruebas de la API."""

from dataclasses import dataclass
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.application.ports import ClassificationFilters
from app.domain.entities import Classification, Organization, User
from app.infrastructure.repositories.uow import SqlUnitOfWork
from app.infrastructure.security.passwords import Argon2PasswordHasher
from tests.factories import seed_api_key, seed_login_user

CSRF = {"X-PQRS-CSRF": "1"}
PASSWORD = "contraseña-segura-1"


@dataclass
class Tenant:
    org: Organization
    user: User
    email: str
    api_key: str
    api_key_id: UUID

    @property
    def key_headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key}


def make_tenant(
    db: Session, hasher: Argon2PasswordHasher, name: str = "Org A", email: str = "ana@example.com"
) -> Tenant:
    org, user = seed_login_user(db, hasher, email=email, org_name=name)
    raw, key = seed_api_key(db, org.id)
    return Tenant(org, user, email, raw, key.id)


def log_in(client: TestClient, tenant: Tenant) -> None:
    response = client.post("/api/v1/auth/login", json={"email": tenant.email, "password": PASSWORD})
    assert response.status_code == 200, response.text


def stored(db: Session, org_id: UUID) -> list[Classification]:
    """Clasificaciones guardadas de una organización, del más reciente al más antiguo."""
    return SqlUnitOfWork(db).classifications.search(
        org_id, ClassificationFilters(), limit=1000, offset=0
    )
