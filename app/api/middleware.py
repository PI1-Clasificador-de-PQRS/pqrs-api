import json
import logging
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import DEFAULT_MESSAGES, STATUS_BY_CODE, error_body
from app.domain.errors import ErrorCode
from app.infrastructure.logging import request_id_var

logger = logging.getLogger("pqrs.access")


def _level_for(status: int) -> int:
    if status == 503:  # esperado: un motor no está disponible
        return logging.WARNING
    return logging.ERROR if status >= 500 else logging.INFO


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
                _level_for(status),
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


class _PayloadTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Rechaza con 413 ``payload_too_large`` los cuerpos de más de ``max_bytes``.

    Verifica ``Content-Length`` antes de leer nada y, además, cuenta los bytes del cuerpo que
    llegan (cuerpos con ``Transfer-Encoding: chunked`` o con un ``Content-Length`` falso). Es ASGI
    puro y nunca guarda ni registra el cuerpo.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                too_large = int(declared) > self.max_bytes
            except ValueError:
                too_large = False  # el servidor HTTP ya rechaza un Content-Length inválido
            if too_large:
                await self._reject(send)
                return

        received = 0
        exceeded = False
        response_started = False
        replacement_sent = False

        async def counting_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    exceeded = True
                    raise _PayloadTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started, replacement_sent
            if exceeded:
                # FastAPI convierte cualquier fallo al leer el cuerpo en un 400: se sustituye su
                # respuesta por el 413 (y se descarta lo que siga de ella).
                if not replacement_sent:
                    replacement_sent = True
                    await self._reject(send)
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _PayloadTooLarge:
            if response_started:
                raise
            if not replacement_sent:
                await self._reject(send)

    async def _reject(self, send: Send) -> None:
        code = ErrorCode.PAYLOAD_TOO_LARGE
        body = json.dumps(error_body(code, DEFAULT_MESSAGES[code])).encode()
        await send(
            {
                "type": "http.response.start",
                "status": STATUS_BY_CODE[code],
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
