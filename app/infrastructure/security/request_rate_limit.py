import threading
from collections import OrderedDict, deque
from datetime import datetime, timedelta
from math import ceil

from app.application.ports import Clock


class InMemoryRequestRateLimiter:
    """Ventana deslizante de solicitudes por clave (API key o usuario), en memoria.

    Asume una sola réplica. Nunca se usa la IP como clave. La memoria está acotada por
    ``max_keys``: las claves se ordenan por su última solicitud y, si se supera el tope, se
    desaloja primero la de actividad más antigua.
    """

    def __init__(self, *, window: timedelta, max_keys: int, clock: Clock) -> None:
        self._window = window
        self._max_keys = max_keys
        self._clock = clock
        self._hits: OrderedDict[str, deque[datetime]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._hits)

    def hit(self, key: str, limit: int) -> int | None:
        """Registra una solicitud. ``None`` si pasa; si no, segundos hasta que pueda reintentar.

        Una solicitud rechazada no se cuenta: no alarga el bloqueo de quien insiste.
        """
        now = self._clock.now()
        cutoff = now - self._window
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = deque()
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                self._hits[key] = hits
                self._hits.move_to_end(key)
                wait = (hits[0] + self._window - now).total_seconds()
                return max(1, ceil(wait))
            hits.append(now)
            self._hits[key] = hits
            self._hits.move_to_end(key)
            while len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)
            return None
