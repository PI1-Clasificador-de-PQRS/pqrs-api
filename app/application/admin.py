"""Casos de uso de administración (los invoca la CLI ``pqrs-admin``)."""

from uuid import UUID

from app.application.passwords import validate_password_policy
from app.application.ports import Clock, PasswordHasher, UnitOfWork
from app.domain.credentials import generate_api_key, hash_token
from app.domain.entities import ApiKey, Organization, User
from app.domain.errors import DomainError, ErrorCode


def _not_found(message: str) -> DomainError:
    return DomainError(ErrorCode.NOT_FOUND, message)


class AdminService:
    def __init__(self, uow: UnitOfWork, *, hasher: PasswordHasher, clock: Clock) -> None:
        self._uow = uow
        self._hasher = hasher
        self._clock = clock

    def _require_org(self, org_id: UUID) -> Organization:
        org = self._uow.organizations.get(org_id)
        if org is None:
            raise _not_found("Organización no encontrada.")
        return org

    def create_org(self, name: str) -> Organization:
        name = name.strip()
        if not name:
            raise DomainError(
                ErrorCode.INVALID_REQUEST, "El nombre de la organización es obligatorio."
            )
        org = self._uow.organizations.add(name=name, created_at=self._clock.now())
        self._uow.commit()
        return org

    def list_orgs(self) -> list[Organization]:
        return self._uow.organizations.list()

    def create_user(self, org_id: UUID, *, email: str, name: str, password: str) -> User:
        self._require_org(org_id)
        email = email.strip().lower()
        if "@" not in email or email.startswith("@") or email.endswith("@"):
            raise DomainError(ErrorCode.INVALID_REQUEST, "El correo no es válido.")
        if not name.strip():
            raise DomainError(ErrorCode.INVALID_REQUEST, "El nombre del usuario es obligatorio.")
        validate_password_policy(password)
        user = self._uow.users.add(
            org_id,
            email=email,
            name=name.strip(),
            password_hash=self._hasher.hash(password),
            created_at=self._clock.now(),
        )
        self._uow.commit()
        return user

    def reset_password(self, org_id: UUID, *, email: str, password: str) -> int:
        """Cambia la contraseña y cierra las sesiones abiertas. Devuelve cuántas cerró."""
        user = self._uow.users.get_by_email(org_id, email)
        if user is None:
            raise _not_found("Usuario no encontrado.")
        validate_password_policy(password)
        self._uow.users.set_password_hash(org_id, user.id, self._hasher.hash(password))
        closed = self._uow.sessions.delete_for_user(org_id, user.id)
        self._uow.commit()
        return closed

    def deactivate_user(self, org_id: UUID, *, email: str) -> int:
        """Desactiva al usuario y borra todas sus sesiones. Devuelve cuántas cerró."""
        user = self._uow.users.get_by_email(org_id, email)
        if user is None:
            raise _not_found("Usuario no encontrado.")
        self._uow.users.deactivate(org_id, user.id)
        closed = self._uow.sessions.delete_for_user(org_id, user.id)
        self._uow.commit()
        return closed

    def create_key(self, org_id: UUID, *, name: str) -> tuple[str, ApiKey]:
        """Devuelve ``(clave_en_claro, ApiKey)``. La clave en claro no se vuelve a poder ver."""
        self._require_org(org_id)
        if not name.strip():
            raise DomainError(ErrorCode.INVALID_REQUEST, "El nombre de la clave es obligatorio.")
        raw, prefix = generate_api_key()
        key = self._uow.api_keys.add(
            org_id,
            name=name.strip(),
            prefix=prefix,
            key_hash=hash_token(raw),
            created_at=self._clock.now(),
        )
        self._uow.commit()
        return raw, key

    def revoke_key(self, org_id: UUID, key_id: UUID) -> None:
        key = self._uow.api_keys.get(org_id, key_id)
        if key is None:
            raise _not_found("Clave no encontrada.")
        if key.revoked_at is not None:
            raise DomainError(ErrorCode.INVALID_REQUEST, "La clave ya estaba revocada.")
        self._uow.api_keys.revoke(org_id, key_id, self._clock.now())
        self._uow.commit()

    def list_keys(self, org_id: UUID) -> list[ApiKey]:
        return self._uow.api_keys.list(org_id)
