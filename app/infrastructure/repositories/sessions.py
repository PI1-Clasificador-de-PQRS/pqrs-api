from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.domain.entities import AuthSession
from app.infrastructure.db.models import SessionModel


def _to_entity(row: SessionModel) -> AuthSession:
    return AuthSession(
        id=row.id,
        user_id=row.user_id,
        org_id=row.org_id,
        token_hash=row.token_hash,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
    )


class SqlSessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self, org_id: UUID, *, user_id: UUID, token_hash: str, created_at: datetime
    ) -> AuthSession:
        row = SessionModel(
            org_id=org_id,
            user_id=user_id,
            token_hash=token_hash,
            created_at=created_at,
            last_seen_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return _to_entity(row)

    def touch(self, org_id: UUID, session_id: UUID, last_seen_at: datetime) -> None:
        self._session.execute(
            update(SessionModel)
            .where(SessionModel.org_id == org_id, SessionModel.id == session_id)
            .values(last_seen_at=last_seen_at)
        )

    def delete(self, org_id: UUID, session_id: UUID) -> bool:
        result = self._session.execute(
            delete(SessionModel).where(SessionModel.org_id == org_id, SessionModel.id == session_id)
        )
        return result.rowcount > 0  # type: ignore[attr-defined]

    def delete_for_user(self, org_id: UUID, user_id: UUID) -> int:
        result = self._session.execute(
            delete(SessionModel).where(
                SessionModel.org_id == org_id, SessionModel.user_id == user_id
            )
        )
        return result.rowcount  # type: ignore[attr-defined]

    def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        row = self._session.scalar(
            select(SessionModel).where(SessionModel.token_hash == token_hash)
        )
        return _to_entity(row) if row else None
