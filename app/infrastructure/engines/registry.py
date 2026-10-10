"""Registro de motores: carga el motor local una vez, protege el LLM con un circuito.

El circuito, el semáforo y los límites en memoria asumen **una sola réplica** de la API.
"""

import json
import logging
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TypeVar

from pqrs_classifier.engines import ClassifierEngine, LLMEngine
from pqrs_classifier.engines.base import Engine, EngineError, Prediction

from app.application.circuit_breaker import CircuitBreaker
from app.application.ports import Clock, EngineStatus
from app.domain.categories import Engine as EngineName
from app.domain.errors import ErrorCode, ServiceUnavailable
from app.infrastructure.config.settings import Settings

logger = logging.getLogger("pqrs.engines")

T = TypeVar("T")

NOT_CONFIGURED = "not_configured"
TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
NOT_LOADED = "not_loaded"

_UNAVAILABLE_MESSAGES = {
    (EngineName.LLM, NOT_CONFIGURED): "El motor LLM no está configurado.",
    (EngineName.LLM, TEMPORARILY_UNAVAILABLE): "El motor LLM no está disponible temporalmente.",
    (EngineName.CLASSIFIER, NOT_LOADED): "El clasificador local no está cargado.",
}
_FAILED_MESSAGES = {
    EngineName.LLM: "El motor LLM falló al clasificar.",
    EngineName.CLASSIFIER: "El clasificador local falló al clasificar.",
}
_ERROR_CODES = {
    EngineName.LLM: ErrorCode.LLM_UNAVAILABLE,
    EngineName.CLASSIFIER: ErrorCode.CLASSIFIER_UNAVAILABLE,
}


def read_classifier_f1(model_dir: Path) -> float | None:
    """F1 macro registrado en ``metadata.json`` del modelo local, si se puede leer."""
    try:
        meta = json.loads((model_dir / "metadata.json").read_text(encoding="utf-8"))
        return float(meta["metrics"]["f1_macro"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


class EngineRegistry:
    """Motores disponibles y su estado. Implementa ``EngineGateway``.

    Sin argumentos de motor, ``load()`` carga el local desde ``MODEL_DIR`` y crea el LLM desde la
    configuración. Las pruebas inyectan motores ya construidos (``FakeEngine``).
    """

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        *,
        llm: Engine | None = None,
        classifier: Engine | None = None,
        classifier_f1: float | None = None,
    ) -> None:
        self._settings = settings
        self._llm = llm
        self._classifier = classifier
        self._classifier_f1 = classifier_f1
        self._injected = llm is not None or classifier is not None
        self._loaded = self._injected
        self._breaker = CircuitBreaker(
            clock,
            failures=settings.llm_circuit_failures,
            open_seconds=settings.llm_circuit_open_seconds,
        )
        self._classifier_slots = threading.BoundedSemaphore(settings.classifier_max_concurrency)

    @property
    def breaker(self) -> CircuitBreaker:
        return self._breaker

    def load(self) -> None:
        """Carga el motor local una sola vez. Si falla, la API sigue y queda ``not_loaded``."""
        if self._loaded:
            return
        self._loaded = True
        self._llm = LLMEngine.from_env(self._settings.llm_env())
        model_dir = self._settings.model_dir
        self._classifier_f1 = read_classifier_f1(model_dir)
        try:
            self._classifier = ClassifierEngine(model_dir)
        except Exception as exc:  # artefactos ausentes o incompatibles, falta torch, etc.
            # Solo el tipo: el mensaje podría arrastrar rutas o detalles del entorno.
            logger.error(
                "classifier_load_failed",
                extra={"engine": "classifier", "exception_type": type(exc).__name__},
            )
            self._classifier = None
        else:
            logger.info("classifier_loaded", extra={"engine": "classifier"})

    @property
    def classifier_loaded(self) -> bool:
        return self._classifier is not None and self._engine_available(self._classifier)

    @staticmethod
    def _engine_available(engine: Engine) -> bool:
        try:
            return engine.is_available()[0]
        except Exception:
            return False

    def _status(self, engine: EngineName) -> EngineStatus:
        if engine is EngineName.LLM:
            if self._llm is None or not self._engine_available(self._llm):
                return EngineStatus(engine.value, False, NOT_CONFIGURED, None, self._llm_f1())
            if not self._breaker.allows():
                retry_after = self._breaker.retry_after()
                return EngineStatus(
                    engine.value, False, TEMPORARILY_UNAVAILABLE, retry_after, self._llm_f1()
                )
            return EngineStatus(engine.value, True, None, None, self._llm_f1())
        if not self.classifier_loaded:
            return EngineStatus(engine.value, False, NOT_LOADED, None, self._classifier_f1)
        return EngineStatus(engine.value, True, None, None, self._classifier_f1)

    def _llm_f1(self) -> float:
        return self._settings.llm_eval_f1_macro

    def status(self) -> list[EngineStatus]:
        return [self._status(EngineName.LLM), self._status(EngineName.CLASSIFIER)]

    def select(self, requested: str | None) -> str:
        if requested is not None:
            engine = EngineName(requested)
            self._require_available(engine)
            return engine.value
        if self._status(EngineName.LLM).available:
            return EngineName.LLM.value
        self._require_available(EngineName.CLASSIFIER)
        return EngineName.CLASSIFIER.value

    def _require_available(self, engine: EngineName) -> None:
        status = self._status(engine)
        if status.available:
            return
        assert status.reason is not None
        raise ServiceUnavailable(
            _ERROR_CODES[engine], _UNAVAILABLE_MESSAGES[(engine, status.reason)], status.retry_after
        )

    def classify(self, engine_id: str, text: str) -> Prediction:
        return self._run(engine_id, lambda engine: engine.classify(text))

    def classify_batch(self, engine_id: str, texts: Sequence[str]) -> list[Prediction]:
        return self._run(engine_id, lambda engine: engine.classify_batch(texts))

    def _run(self, engine_id: str, call: Callable[[Engine], T]) -> T:
        name = EngineName(engine_id)
        if name is EngineName.LLM:
            return self._run_llm(call)
        return self._run_classifier(call)

    def _run_llm(self, call: Callable[[Engine], T]) -> T:
        engine = self._llm
        if engine is None:
            self._require_available(EngineName.LLM)
            raise AssertionError("unreachable")  # pragma: no cover
        if not self._breaker.acquire():
            self._require_available(EngineName.LLM)
            raise ServiceUnavailable(
                ErrorCode.LLM_UNAVAILABLE,
                _UNAVAILABLE_MESSAGES[(EngineName.LLM, TEMPORARILY_UNAVAILABLE)],
                self._breaker.retry_after(),
            )
        try:
            result = call(engine)
        except BaseException as exc:
            # Todo fallo cuenta, incluso uno inesperado; así un sondeo semiabierto nunca queda
            # sin resolver.
            error = exc if isinstance(exc, EngineError) else EngineError()
            self._breaker.record_failure(error)
            logger.warning(
                "engine_failed",
                extra={
                    "engine": "llm",
                    "exception_type": type(exc).__name__,
                    "circuit_state": self._breaker.state,
                },
            )
            if not isinstance(exc, Exception):
                raise
            raise ServiceUnavailable(
                ErrorCode.LLM_UNAVAILABLE,
                _FAILED_MESSAGES[EngineName.LLM],
                self._breaker.retry_after(),
            ) from None
        self._breaker.record_success()
        return result

    def _run_classifier(self, call: Callable[[Engine], T]) -> T:
        engine = self._classifier
        if engine is None:
            self._require_available(EngineName.CLASSIFIER)
            raise AssertionError("unreachable")  # pragma: no cover
        try:
            with self._classifier_slots:
                return call(engine)
        except Exception as exc:
            logger.warning(
                "engine_failed",
                extra={"engine": "classifier", "exception_type": type(exc).__name__},
            )
            raise ServiceUnavailable(
                ErrorCode.CLASSIFIER_UNAVAILABLE, _FAILED_MESSAGES[EngineName.CLASSIFIER]
            ) from None
