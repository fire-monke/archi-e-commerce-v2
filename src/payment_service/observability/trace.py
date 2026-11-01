from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import uuid4

SERVICE_NAME = "payment-service"

_current_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    return uuid4().hex


def current_trace_id() -> str | None:
    return _current_trace_id.get()


def bind_trace_id(trace_id: str) -> Token[str | None]:
    """Attach a traceId to the current execution context (request, task or thread)."""
    return _current_trace_id.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    _current_trace_id.reset(token)


@contextmanager
def trace_scope(trace_id: str | None = None) -> Iterator[str]:
    """Run a block under a traceId: the given one, the current one, or a new one."""
    effective = trace_id or current_trace_id() or new_trace_id()
    token = bind_trace_id(effective)
    try:
        yield effective
    finally:
        reset_trace_id(token)
