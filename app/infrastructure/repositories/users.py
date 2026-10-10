from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.entities import User
from app.domain.errors import DomainError, ErrorCode
from app.infrastructure.db.models import UserModel


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _to_entity(row: UserModel) -> User:
    return User(
        id=row.id,
        org_id=row.org_id,
        email=row.email,
        name=row.name,
        password_hash=row.password_hash,
        active=row.active,
        created_at=row.created_at,
    )


class SqlUserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        org_id: UUID,
        *,
        email: str,
        name: str,
        password_hash: str,
        created_at: datetime,
    ) -> User:
        row = UserModel(
            org_id=org_id,
            email=normalize_email(email),
            name=name,
            password_hash=password_hash,
            active=True,
            created_at=created_at,
        )
        try:
            # Savepoint: un correo duplicado no debe invalidar la transacción completa.
            with self._session.begin_nested():
                self._session.add(row)
        except IntegrityError as exc:
            raise DomainError(
                ErrorCode.INVALID_REQUEST, "Ya existe un usuario con ese correo."
            ) from exc
        return _to_entity(row)

    def get(self, org_id: UUID, user_id: UUID) -> User | None:
        row = self._session.scalar(
            select(UserModel).where(UserModel.org_id == org_id, UserModel.id == user_id)
        )
        return _to_entity(row) if row else None

    def get_by_email(self, org_id: UUID, email: str) -> User | None:
        row = self._session.scalar(
            select(UserModel).where(
                UserModel.org_id == org_id, func.lower(UserModel.email) == normalize_email(email)
            )
        )
        return _to_entity(row) if row else None

    def list(self, org_id: UUID) -> list[User]:
        rows = self._session.scalars(
            select(UserModel).where(UserModel.org_id == org_id).order_by(UserModel.created_at)
        )
        return [_to_entity(r) for r in rows]

    def set_password_hash(self, org_id: UUID, user_id: UUID, password_hash: str) -> bool:
        result = self._session.execute(
            update(UserModel)
            .where(UserModel.org_id == org_id, UserModel.id == user_id)
            .values(password_hash=password_hash)
        )
        return result.rowcount > 0  # type: ignore[attr-defined]

    def deactivate(self, org_id: UUID, user_id: UUID) -> bool:
        result = self._session.execute(
            update(UserModel)
            .where(UserModel.org_id == org_id, UserModel.id == user_id)
            .values(active=False)
        )
        return result.rowcount > 0  # type: ignore[attr-defined]

    def get_for_login(self, email: str) -> User | None:
        row = self._session.scalar(
            select(UserModel).where(func.lower(UserModel.email) == normalize_email(email))
        )
        return _to_entity(row) if row else None
