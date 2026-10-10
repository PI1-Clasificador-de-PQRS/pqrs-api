from datetime import UTC, datetime, timedelta


class FakeClock:
    """Reloj controlable para las pruebas de caducidad y de ventanas de tiempo."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)
