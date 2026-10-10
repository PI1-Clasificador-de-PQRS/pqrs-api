from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.entities import Organization
from app.infrastructure.db.models import OrganizationModel


def _to_entity(row: OrganizationModel) -> Organization:
    return Organization(id=row.id, name=row.name, active=row.active, created_at=row.created_at)


class SqlOrganizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, *, name: str, created_at: datetime) -> Organization:
        row = OrganizationModel(name=name, active=True, created_at=created_at)
        self._session.add(row)
        self._session.flush()
        return _to_entity(row)

    def get(self, org_id: UUID) -> Organization | None:
        row = self._session.get(OrganizationModel, org_id)
        return _to_entity(row) if row else None

    def list(self) -> list[Organization]:
        rows = self._session.scalars(select(OrganizationModel).order_by(OrganizationModel.name))
        return [_to_entity(r) for r in rows]
