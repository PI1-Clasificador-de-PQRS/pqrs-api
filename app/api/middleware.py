import logging
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.infrastructure.logging import request_id_var

logger = logging.getLogger("pqrs.access")


class RequestLogMiddleware:
    """Asigna un id a cada petición y registra una línea por petición.

    Es ASGI puro: nunca lee el cuerpo, las cookies ni las cabeceras de credenciales. Solo se
    registran método, ruta (sin query string), código de respuesta y latencia.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            logger.log(
                logging.ERROR if status >= 500 else logging.INFO,
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "request_id": request_id,
                },
            )
            request_id_var.reset(token)
