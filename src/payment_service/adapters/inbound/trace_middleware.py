import logging
from time import perf_counter

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from payment_service.observability.trace import bind_trace_id, new_trace_id, reset_trace_id

logger = logging.getLogger(__name__)

TRACE_HEADER = "X-Trace-Id"


class TraceIdMiddleware:
    """Entry point of every HTTP flow: reuse X-Trace-Id or generate one, log the request with its duration."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        token = bind_trace_id(Headers(scope=scope).get(TRACE_HEADER) or new_trace_id())
        started = perf_counter()
        method, path = scope["method"], scope["path"]
        status_code = 500

        async def send_and_log(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                logger.info(
                    "HTTP %s %s completed with %s", method, path, status_code, extra={"duration_ms": _elapsed_ms(started)}
                )

        try:
            await self.app(scope, receive, send_and_log)
        finally:
            reset_trace_id(token)


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 2)
