"""Circuito de protección del motor LLM (en memoria; asume una sola réplica)."""

import math
import threading
from datetime import datetime, timedelta

from pqrs_classifier.engines.base import EngineError, EngineUnavailableError

from app.application.ports import Clock


class CircuitBreaker:
    """Cerrado → abierto → semiabierto → cerrado.

    * Se abre tras ``failures`` fallos consecutivos (``EngineError``) o de inmediato con un
      ``EngineUnavailableError`` de tipo ``rate_limited``.
    * Permanece abierto ``open_seconds`` o lo que indique ``retry_after``, lo que sea mayor.
    * Pasado ese tiempo queda semiabierto: la siguiente solicitud (solo una a la vez) prueba el
      motor. Si tiene éxito se cierra; si falla se abre otra vez.
    * Un éxito reinicia el contador de fallos.
    """

    def __init__(self, clock: Clock, *, failures: int = 3, open_seconds: int = 60) -> None:
        self._clock = clock
        self._max_failures = failures
        self._open_seconds = open_seconds
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until: datetime | None = None
        self._probing = False

    @property
    def state(self) -> str:
        """``closed``, ``open`` o ``half_open`` (solo informativo)."""
        with self._lock:
            return self._state()

    def _state(self) -> str:
        if self._open_until is None:
            return "closed"
        if self._clock.now() < self._open_until or self._probing:
            return "open"
        return "half_open"

    def retry_after(self) -> int | None:
        """Segundos que faltan para el sondeo si está abierto; ``None`` si no lo está."""
        with self._lock:
            return self._remaining()

    def _remaining(self) -> int | None:
        if self._open_until is None:
            return None
        remaining = (self._open_until - self._clock.now()).total_seconds()
        if remaining <= 0:
            # Semiabierto: el siguiente sondeo ya puede pasar. Con un sondeo en curso, el resto
            # espera un instante a su resultado.
            return 1 if self._probing else None
        return max(1, math.ceil(remaining))

    def allows(self) -> bool:
        """¿Una solicitud nueva pasaría? No consume el sondeo del estado semiabierto."""
        with self._lock:
            return self._state() in ("closed", "half_open")

    def acquire(self) -> bool:
        """Pide permiso para llamar al motor. En semiabierto solo lo obtiene un solicitante."""
        with self._lock:
            state = self._state()
            if state == "closed":
                return True
            if state == "half_open":
                self._probing = True
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = None
            self._probing = False

    def record_failure(self, exc: EngineError) -> None:
        with self._lock:
            probing, self._probing = self._probing, False
            if isinstance(exc, EngineUnavailableError) and exc.reason == "rate_limited":
                self._open(exc.retry_after)
                return
            self._failures += 1
            if probing or self._failures >= self._max_failures:
                self._open(None)

    def _open(self, retry_after: float | None) -> None:
        seconds = max(float(self._open_seconds), retry_after or 0.0)
        self._open_until = self._clock.now() + timedelta(seconds=seconds)
        self._failures = 0
