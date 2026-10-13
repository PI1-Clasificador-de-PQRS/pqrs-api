from threading import Lock

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker


class PgTimezoneCatalog:
    """Zonas que reconoce PostgreSQL, leídas de ``pg_timezone_names`` una vez por proceso.

    La vista lee el directorio de zonas del servidor en cada consulta (decenas de ms), así que
    el resultado se guarda en memoria. Si cambia el tzdata del servidor, basta reiniciar la API.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._names: frozenset[str] | None = None
        self._lock = Lock()

    def names(self) -> frozenset[str]:
        if self._names is None:
            with self._lock:
                if self._names is None:
                    with self._session_factory() as session:
                        rows = session.scalars(text("SELECT name FROM pg_timezone_names"))
                        self._names = frozenset(rows)
        return self._names
