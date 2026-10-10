import threading
from collections import deque
from datetime import datetime, timedelta

from app.application.ports import Clock

_SWEEP_EVERY = timedelta(minutes=1)


class InMemoryLoginRateLimiter:
    """Ventana deslizante de intentos fallidos por email y por IP.

    Es en memoria: sirve para una sola réplica. Cuando una clave acumula
    ``max_attempts`` fallos dentro de la ventana, se bloquea hasta que el más antiguo salga.
    """

    def __init__(self, *, max_attempts: int, window: timedelta, clock: Clock) -> None:
        self._max_attempts = max_attempts
        self._window = window
        self._clock = clock
        self._failures: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()
        self._last_sweep = clock.now()

    @staticmethod
    def _keys(email: str, ip: str | None) -> list[str]:
        keys = [f"email:{email.strip().lower()}"]
        if ip:
            keys.append(f"ip:{ip}")
        return keys

    def _prune(self, key: str, now: datetime) -> deque[datetime] | None:
        attempts = self._failures.get(key)
        if attempts is None:
            return None
        cutoff = now - self._window
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
            return None
        return attempts

    def _sweep(self, now: datetime) -> None:
        if now - self._last_sweep < _SWEEP_EVERY:
            return
        self._last_sweep = now
        for key in list(self._failures):
            self._prune(key, now)

    def is_blocked(self, *, email: str, ip: str | None) -> bool:
        now = self._clock.now()
        with self._lock:
            self._sweep(now)
            for key in self._keys(email, ip):
                attempts = self._prune(key, now)
                if attempts is not None and len(attempts) >= self._max_attempts:
                    return True
        return False

    def record_failure(self, *, email: str, ip: str | None) -> None:
        now = self._clock.now()
        with self._lock:
            for key in self._keys(email, ip):
                self._failures.setdefault(key, deque()).append(now)

    def record_success(self, *, email: str) -> None:
        with self._lock:
            self._failures.pop(f"email:{email.strip().lower()}", None)
