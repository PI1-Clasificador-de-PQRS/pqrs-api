"""Historial y métricas de una organización. Todo se filtra por ``org_id`` en el repositorio."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from app.application.ports import ClassificationFilters, UnitOfWork
from app.domain.categories import CATEGORIES, ENGINES, SOURCES
from app.domain.entities import Classification
from app.domain.errors import DomainError, ErrorCode

# Tope de periodos de la serie temporal: evita que un rango enorme (p. ej. desde el año 1) genere
# millones de puntos.
MAX_TIMESERIES_PERIODS = 400

GRANULARITIES = ("day", "week")


def day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def build_filters(
    date_from: date | None,
    date_to: date | None,
    *,
    category: str | None = None,
    engine: str | None = None,
    source: str | None = None,
    batch_id: UUID | None = None,
) -> ClassificationFilters:
    """Fechas UTC inclusivas → ``[date_from 00:00, date_to + 1 día 00:00)``."""
    if date_from is not None and date_to is not None and date_from > date_to:
        raise DomainError(
            ErrorCode.INVALID_REQUEST, "`date_from` no puede ser posterior a `date_to`."
        )
    return ClassificationFilters(
        created_from=day_start(date_from) if date_from is not None else None,
        created_before=(
            day_start(date_to + timedelta(days=1))
            if date_to is not None and date_to < date.max
            else None
        ),
        category=category,
        engine=engine,
        source=source,
        batch_id=batch_id,
    )


def period_start(day: date, granularity: str) -> date:
    """Primer día del periodo: el propio día o el lunes de su semana (como ``date_trunc``)."""
    return day - timedelta(days=day.weekday()) if granularity == "week" else day


def _empty_counts(keys: tuple[str, ...], counts: dict[str, int]) -> dict[str, int]:
    return {key: counts.get(key, 0) for key in keys}


class HistoryService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def list(
        self, org_id: UUID, filters: ClassificationFilters, *, page: int, page_size: int
    ) -> tuple[list[Classification], int]:
        repo = self._uow.classifications
        items = repo.search(org_id, filters, limit=page_size, offset=(page - 1) * page_size)
        return items, repo.count(org_id, filters)

    def get(self, org_id: UUID, classification_id: UUID) -> Classification | None:
        return self._uow.classifications.get(org_id, classification_id)


@dataclass(frozen=True, slots=True)
class MetricsData:
    total: int
    by_category: dict[str, int]
    by_engine: dict[str, int]
    by_source: dict[str, int]
    timeseries: list[tuple[date, int]]


class MetricsService:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def summary(
        self,
        org_id: UUID,
        *,
        date_from: date | None,
        date_to: date | None,
        granularity: str,
    ) -> MetricsData:
        filters = build_filters(date_from, date_to)
        repo = self._uow.classifications
        counts = repo.counts(org_id, filters)
        return MetricsData(
            total=counts.total,
            by_category=_empty_counts(CATEGORIES, counts.by_category),
            by_engine=_empty_counts(ENGINES, counts.by_engine),
            by_source=_empty_counts(SOURCES, counts.by_source),
            timeseries=self._timeseries(org_id, filters, date_from, date_to, granularity),
        )

    def _timeseries(
        self,
        org_id: UUID,
        filters: ClassificationFilters,
        date_from: date | None,
        date_to: date | None,
        granularity: str,
    ) -> list[tuple[date, int]]:
        repo = self._uow.classifications
        # Sin fecha en un extremo, la serie llega hasta la primera o última clasificación.
        if date_from is None or date_to is None:
            bounds = repo.bounds(org_id, filters)
            if bounds is None:
                return []
            first, last = bounds
            date_from = date_from or first.astimezone(UTC).date()
            date_to = date_to or last.astimezone(UTC).date()
        start, end = period_start(date_from, granularity), period_start(date_to, granularity)
        step = timedelta(weeks=1) if granularity == "week" else timedelta(days=1)
        periods = (end - start) // step + 1
        if periods > MAX_TIMESERIES_PERIODS:
            raise DomainError(
                ErrorCode.INVALID_REQUEST,
                f"El rango abarca {periods} periodos; el máximo es {MAX_TIMESERIES_PERIODS}. "
                "Reduce el rango o usa `granularity=week`.",
            )
        counted = repo.timeseries(org_id, filters, granularity=granularity)
        return [(start + step * i, counted.get(start + step * i, 0)) for i in range(periods)]
