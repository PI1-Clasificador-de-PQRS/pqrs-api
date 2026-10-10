from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.entities import ApiKey
from app.infrastructure.db.models import ApiKeyModel


def _to_entity(row: ApiKeyModel) -> ApiKey:
    return ApiKey(
        id=row.id,
        org_id=row.org_id,
        name=row.name,
        prefix=row.prefix,
        key_hash=row.key_hash,
        active=row.active,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


class SqlApiKeyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self, org_id: UUID, *, name: str, prefix: str, key_hash: str, created_at: datetime
    ) -> ApiKey:
        row = ApiKeyModel(
            org_id=org_id,
            name=name,
            prefix=prefix,
            key_hash=key_hash,
            active=True,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return _to_entity(row)

    def get(self, org_id: UUID, key_id: UUID) -> ApiKey | None:
        row = self._session.scalar(
            select(ApiKeyModel).where(ApiKeyModel.org_id == org_id, ApiKeyModel.id == key_id)
        )
        return _to_entity(row) if row else None

    def list(self, org_id: UUID) -> list[ApiKey]:
        rows = self._session.scalars(
            select(ApiKeyModel).where(ApiKeyModel.org_id == org_id).order_by(ApiKeyModel.created_at)
        )
        return [_to_entity(r) for r in rows]

    def revoke(self, org_id: UUID, key_id: UUID, revoked_at: datetime) -> bool:
        result = self._session.execute(
            update(ApiKeyModel)
            .where(
                ApiKeyModel.org_id == org_id,
                ApiKeyModel.id == key_id,
                ApiKeyModel.revoked_at.is_(None),
            )
            .values(active=False, revoked_at=revoked_at)
        )
        return result.rowcount > 0  # type: ignore[attr-defined]

    def touch_last_used(self, org_id: UUID, key_id: UUID, used_at: datetime) -> None:
        self._session.execute(
            update(ApiKeyModel)
            .where(ApiKeyModel.org_id == org_id, ApiKeyModel.id == key_id)
            .values(last_used_at=used_at)
        )

    def get_by_hash(self, key_hash: str) -> ApiKey | None:
        row = self._session.scalar(select(ApiKeyModel).where(ApiKeyModel.key_hash == key_hash))
        return _to_entity(row) if row else None
