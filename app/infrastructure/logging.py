import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Único conjunto de campos extra que llega a la salida. Lo que no esté aquí se descarta, de
# modo que ningún registro pueda arrastrar por descuido cuerpos, cookies ni credenciales.
ALLOWED_EXTRA_FIELDS = (
    "method",
    "path",
    "status",
    "latency_ms",
    "request_id",
    "exception_type",
    "engine",
    "circuit_state",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ALLOWED_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if "request_id" not in payload and (rid := request_id_var.get()):
            payload["request_id"] = rid
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """Logging JSON a stdout. Se llama una vez al arrancar el proceso."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # Los parámetros de las consultas incluirían hashes y datos de usuarios.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    # El acceso lo registra el middleware de la aplicación, sin query string.
    logging.getLogger("uvicorn.access").disabled = True
