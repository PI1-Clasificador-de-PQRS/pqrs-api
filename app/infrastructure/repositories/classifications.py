from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import String, bindparam, func, select
from sqlalchemy.orm import Session

from app.application.ports import ClassificationCounts, ClassificationFilters
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

    @staticmethod
    def _filtered(stmt: Any, org_id: UUID, filters: ClassificationFilters) -> Any:
        """Único lugar donde se filtra: historial y métricas comparten estas condiciones."""
        model = ClassificationModel
        stmt = stmt.where(model.org_id == org_id)
        if filters.created_from is not None:
            stmt = stmt.where(model.created_at >= filters.created_from)
        if filters.created_before is not None:
            stmt = stmt.where(model.created_at < filters.created_before)
        if filters.category is not None:
            stmt = stmt.where(model.category == filters.category)
        if filters.engine is not None:
            stmt = stmt.where(model.engine == filters.engine)
        if filters.source is not None:
            stmt = stmt.where(model.source == filters.source)
        if filters.batch_id is not None:
            stmt = stmt.where(model.batch_id == filters.batch_id)
        return stmt

    def search(
        self, org_id: UUID, filters: ClassificationFilters, *, limit: int, offset: int
    ) -> list[Classification]:
        stmt = self._filtered(select(ClassificationModel), org_id, filters)
        stmt = (
            stmt.order_by(ClassificationModel.created_at.desc(), ClassificationModel.id)
            .limit(limit)
            .offset(offset)
        )
        return [_to_entity(r) for r in self._session.scalars(stmt)]

    def count(self, org_id: UUID, filters: ClassificationFilters) -> int:
        stmt = self._filtered(
            select(func.count()).select_from(ClassificationModel), org_id, filters
        )
        return self._session.scalar(stmt) or 0

    def _group_counts(
        self, org_id: UUID, filters: ClassificationFilters, column: Any
    ) -> dict[str, int]:
        stmt = self._filtered(select(column, func.count()), org_id, filters).group_by(column)
        return {key: n for key, n in self._session.execute(stmt)}

    def counts(self, org_id: UUID, filters: ClassificationFilters) -> ClassificationCounts:
        model = ClassificationModel
        by_category = self._group_counts(org_id, filters, model.category)
        return ClassificationCounts(
            total=sum(by_category.values()),
            by_category=by_category,
            by_engine=self._group_counts(org_id, filters, model.engine),
            by_source=self._group_counts(org_id, filters, model.source),
        )

    def timeseries(
        self, org_id: UUID, filters: ClassificationFilters, *, granularity: str
    ) -> dict[date, int]:
        # ``timezone(:tz, ...)`` pasa el timestamptz a hora local sin zona; los periodos son días
        # locales o semanas que empiezan el lunes local (ISO), como ``date_trunc`` de PostgreSQL.
        # La zona va siempre como parámetro enlazado (ya validada con ``resolve_timezone``).
        tz = bindparam("tz", filters.tz, type_=String)
        period = func.date_trunc(
            granularity, func.timezone(tz, ClassificationModel.created_at)
        ).label("period")
        stmt = self._filtered(select(period, func.count()), org_id, filters).group_by(period)
        return {row_period.date(): n for row_period, n in self._session.execute(stmt)}

    def bounds(
        self, org_id: UUID, filters: ClassificationFilters
    ) -> tuple[datetime, datetime] | None:
        stmt = self._filtered(
            select(
                func.min(ClassificationModel.created_at), func.max(ClassificationModel.created_at)
            ),
            org_id,
            filters,
        )
        first, last = self._session.execute(stmt).one()
        return None if first is None else (first, last)
