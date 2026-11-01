from collections import deque
from datetime import datetime, timezone
import json
import logging
import sys
from threading import Lock
from typing import Any

from payment_service.observability.trace import SERVICE_NAME, current_trace_id

_STANDARD_RECORD_FIELDS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One log record = one JSON object on one line."""

    def __init__(self, service: str = SERVICE_NAME) -> None:
        super().__init__()
        self.service = service

    def to_dict(self, record: logging.LogRecord) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "service": self.service,
            "traceId": getattr(record, "traceId", None) or current_trace_id(),
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and key not in entry:
                entry[key] = value
        if record.exc_info and record.exc_info[1] is not None:
            error = record.exc_info[1]
            entry["error"] = f"{type(error).__name__}: {error}"
        return entry

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(self.to_dict(record), ensure_ascii=False, default=str)


class InMemoryLogStore:
    """Bounded, thread-safe buffer of recent log entries, queryable by traceId."""

    def __init__(self, max_entries: int = 2000) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=max_entries)
        self._lock = Lock()

    def append(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._entries.append(entry)

    def find(self, trace_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._entries)
        if trace_id is not None:
            entries = [entry for entry in entries if entry.get("traceId") == trace_id]
        return entries


class InMemoryLogHandler(logging.Handler):
    def __init__(self, store: InMemoryLogStore, formatter: JsonFormatter) -> None:
        super().__init__()
        self.store = store
        self.json_formatter = formatter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = json.loads(self.json_formatter.format(record))
            self.store.append(entry)
        except Exception:
            self.handleError(record)


log_store = InMemoryLogStore()
_configured = False


def configure_logging(service: str = SERVICE_NAME) -> InMemoryLogStore:
    """Send application logs as JSON lines to stdout and to the in-memory store (idempotent)."""
    global _configured
    if _configured:
        return log_store
    formatter = JsonFormatter(service)

    app_logger = logging.getLogger("payment_service")
    app_logger.setLevel(logging.INFO)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    app_logger.addHandler(stream_handler)
    app_logger.addHandler(InMemoryLogHandler(log_store, formatter))

    _configured = True
    return log_store
