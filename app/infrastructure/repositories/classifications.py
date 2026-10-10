from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.entities import Classification
from app.infrastructure.db.models import ClassificationModel


def _to_entity(row: ClassificationModel) -> Classification:
    return Classification(
        id=row.id,
        org_id=row.org_id,
        text_anonymized=row.text_anonymized,
        category=row.category,
        engine=row.engine,
        model_version=row.model_version,
        prompt_version=row.prompt_version,
        source=row.source,
        batch_id=row.batch_id,
        external_ref=row.external_ref,
        latency_ms=row.latency_ms,
        user_id=row.user_id,
        api_key_id=row.api_key_id,
        created_at=row.created_at,
    )


class SqlClassificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

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
    ) -> Classification:
        row = ClassificationModel(
            org_id=org_id,
            text_anonymized=text_anonymized,
            category=category,
            engine=engine,
            model_version=model_version,
            prompt_version=prompt_version,
            source=source,
            batch_id=batch_id,
            external_ref=external_ref,
            latency_ms=latency_ms,
            user_id=user_id,
            api_key_id=api_key_id,
            created_at=created_at,
        )
        self._session.add(row)
        self._session.flush()
        return _to_entity(row)

    def get(self, org_id: UUID, classification_id: UUID) -> Classification | None:
        row = self._session.scalar(
            select(ClassificationModel).where(
                ClassificationModel.org_id == org_id, ClassificationModel.id == classification_id
            )
        )
        return _to_entity(row) if row else None

    def list(
        self, org_id: UUID, *, limit: int = 50, offset: int = 0, batch_id: UUID | None = None
    ) -> list[Classification]:
        stmt = select(ClassificationModel).where(ClassificationModel.org_id == org_id)
        if batch_id is not None:
            stmt = stmt.where(ClassificationModel.batch_id == batch_id)
        stmt = (
            stmt.order_by(ClassificationModel.created_at.desc(), ClassificationModel.id)
            .limit(limit)
            .offset(offset)
        )
        return [_to_entity(r) for r in self._session.scalars(stmt)]
