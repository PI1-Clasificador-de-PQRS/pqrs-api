from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from app.infrastructure.config.settings import Settings


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(request: Request) -> Iterator[Session]:
    """Sesión por petición. Los casos de uso hacen ``commit`` explícito.

    No se confía en un commit al salir de la dependencia: FastAPI ejecuta ese código
    después de enviar la respuesta, y el cliente podría anticiparse al commit.
    """
    session: Session = request.app.state.session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
