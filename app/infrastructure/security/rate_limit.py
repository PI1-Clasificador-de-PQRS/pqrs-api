import threading
from collections import OrderedDict, deque
from datetime import datetime, timedelta

from app.application.ports import Clock

_UNKNOWN_IP = "unknown"

Key = tuple[str, ...]


class InMemoryLoginRateLimiter:
    """Ventana deslizante de intentos fallidos de login, en memoria (una sola réplica).

    Dos contadores por intento fallido:

    * par **(email normalizado, IP)**, con ``max_attempts``: frena la fuerza bruta sobre una
      cuenta sin que un tercero desde otra IP pueda bloquear al usuario legítimo;
    * **IP sola**, con ``max_attempts_per_ip``: frena a quien prueba muchos emails desde una IP.

    La memoria está acotada por ``max_keys``. Las claves se mantienen ordenadas por su último
    fallo, así que las vencidas quedan al frente: se purgan al insertar y, si aun así se supera el
    tope, se desaloja primero la de actividad más antigua. Costo conocido: quien genere más de
    ``max_keys`` claves distintas dentro de la ventana puede desalojar contadores ajenos.
    """

    def __init__(
        self,
        *,
        max_attempts: int,
        max_attempts_per_ip: int,
        window: timedelta,
        max_keys: int,
        clock: Clock,
    ) -> None:
        self._max_attempts = max_attempts
        self._max_attempts_per_ip = max_attempts_per_ip
        self._window = window
        self._max_keys = max_keys
        self._clock = clock
        self._failures: OrderedDict[Key, deque[datetime]] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        """Número de claves en memoria."""
        with self._lock:
            return len(self._failures)

    @staticmethod
    def _keys(email: str, ip: str | None) -> tuple[Key, Key]:
        ip = ip or _UNKNOWN_IP
        return ("pair", email.strip().lower(), ip), ("ip", ip)

    def _live_count(self, key: Key, now: datetime) -> int:
        attempts = self._failures.get(key)
        if attempts is None:
            return 0
        cutoff = now - self._window
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
            return 0
        return len(attempts)

    def _purge_expired(self, now: datetime) -> None:
        cutoff = now - self._window
        while self._failures:
            oldest = next(iter(self._failures.values()))
            if oldest[-1] > cutoff:
                break
            self._failures.popitem(last=False)

    def is_blocked(self, *, email: str, ip: str | None) -> bool:
        now = self._clock.now()
        pair, ip_key = self._keys(email, ip)
        with self._lock:
            return (
                self._live_count(pair, now) >= self._max_attempts
                or self._live_count(ip_key, now) >= self._max_attempts_per_ip
            )

    def record_failure(self, *, email: str, ip: str | None) -> None:
        now = self._clock.now()
        with self._lock:
            self._purge_expired(now)
            for key in self._keys(email, ip):
                if self._live_count(key, now) == 0:
                    self._failures[key] = deque()
                else:
                    self._failures.move_to_end(key)
                self._failures[key].append(now)
            while len(self._failures) > self._max_keys:
                self._failures.popitem(last=False)

    def record_success(self, *, email: str, ip: str | None) -> None:
        """Limpia el contador del par (email, IP); el de la IP sola se conserva."""
        pair, _ = self._keys(email, ip)
        with self._lock:
            self._failures.pop(pair, None)
