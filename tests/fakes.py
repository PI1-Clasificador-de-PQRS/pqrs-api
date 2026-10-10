from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pqrs_classifier.categories import Category
from pqrs_classifier.engines.base import EngineError, EngineId, Prediction


class FakeClock:
    """Reloj controlable para las pruebas de caducidad y de ventanas de tiempo."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)


class ScriptedEngine:
    """Motor que ejecuta un guion: cada llamada toma el siguiente elemento de ``script``.

    Un elemento ``Exception`` se lanza; ``None`` (o un guion agotado) clasifica con éxito.
    ``FakeEngine`` del paquete no puede simular un 429 (su ``reason`` es siempre ``None``).
    """

    def __init__(
        self,
        id: EngineId = "llm",
        *,
        category: Category = Category.PETICION,
        model_version: str = "scripted-1",
        prompt_version: str | None = None,
        available: bool = True,
    ) -> None:
        self.id: EngineId = id
        self.category = category
        self.model_version = model_version
        self.prompt_version = prompt_version
        self.available = available
        self.script: list[Exception | None] = []
        self.calls: list[list[str]] = []

    def is_available(self) -> tuple[bool, str | None]:
        return (True, None) if self.available else (False, "not_configured")

    def _next(self) -> None:
        step = self.script.pop(0) if self.script else None
        if step is not None:
            raise step

    def classify(self, text: str) -> Prediction:
        return self.classify_batch([text])[0]

    def classify_batch(self, texts: Sequence[str]) -> list[Prediction]:
        self.calls.append(list(texts))
        self._next()
        return [
            Prediction(self.category, self.id, self.model_version, self.prompt_version)
            for _ in texts
        ]


def fail() -> EngineError:
    return EngineError("fallo programado")
