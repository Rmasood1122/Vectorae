"""
Structured logging for Vectorae.

Every log line is JSON with: timestamp, level, service, event, and context fields
(strategy_id, lead_id, user_id, duration_ms, error, request_id).

Uses structlog with the standard library logging backend so it integrates with
Celery's logging configuration without conflict.

Usage:
    from app.core.logging import get_logger

    logger = get_logger("api.strategies")
    logger.info("strategy_created", strategy_id=str(strategy.id), user_id=str(user.id))
    logger.error("pipeline_step_failed", step_no=5, error=str(e), duration_ms=142)
"""
from __future__ import annotations

import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Context variable: request_id bound for the duration of each HTTP request
# ---------------------------------------------------------------------------
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    return _request_id_var.get() or ""


# ---------------------------------------------------------------------------
# structlog configuration
# ---------------------------------------------------------------------------

def _add_request_id(logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Inject the current request_id into every log record."""
    request_id = get_request_id()
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict


def _order_keys(logger: Any, method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Reorder keys so common fields appear first for readability in log viewers:
    timestamp → level → service → event → [context fields]
    """
    ordered: dict[str, Any] = {}
    priority = ["timestamp", "level", "service", "event", "request_id",
                "user_id", "strategy_id", "lead_id", "task_id", "task_name",
                "duration_ms", "error"]
    for key in priority:
        if key in event_dict:
            ordered[key] = event_dict.pop(key)
    ordered.update(event_dict)
    return ordered


def configure_logging(log_level: str = "INFO") -> None:
    """
    Call once at application startup (in app/main.py create_app()).
    Sets up structlog + stdlib logging with JSON output.
    """
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_request_id,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _order_keys,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Quiet noisy third-party loggers
    for noisy in ["httpx", "httpcore", "urllib3", "boto3", "botocore"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(service_name: str) -> structlog.stdlib.BoundLogger:
    """
    Return a structlog logger pre-bound with the service name.

    Example:
        logger = get_logger("pipeline.engine")
        logger.info("step_started", step_no=5, strategy_id="abc-123")
    """
    return structlog.get_logger(service_name).bind(service=service_name)


# ---------------------------------------------------------------------------
# FastAPI middleware: bind request_id per request
# ---------------------------------------------------------------------------

class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Generates (or propagates) a request_id for every incoming HTTP request.
    Binds it to the structlog context so all log lines for the request share it.
    Sets X-Request-ID response header.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Use incoming X-Request-ID if provided, otherwise generate a new one
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = _request_id_var.set(request_id)

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
            structlog.contextvars.clear_contextvars()

        response.headers["X-Request-ID"] = request_id
        return response


# ---------------------------------------------------------------------------
# Celery task logging helpers
# ---------------------------------------------------------------------------

def bind_celery_task_context(
    task_id: str,
    task_name: str,
    strategy_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """
    Call at the start of a Celery task to bind task context to structlog.
    All subsequent log lines within the task will include these fields.
    """
    ctx: dict[str, Any] = {"task_id": task_id, "task_name": task_name}
    if strategy_id:
        ctx["strategy_id"] = strategy_id
    if user_id:
        ctx["user_id"] = user_id
    structlog.contextvars.bind_contextvars(**ctx)


class TaskTimer:
    """Context manager that measures task duration and logs it on exit."""

    def __init__(self, logger: structlog.stdlib.BoundLogger, event: str, **extra: Any):
        self._logger = logger
        self._event = event
        self._extra = extra
        self._start: float = 0.0

    def __enter__(self) -> "TaskTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        duration_ms = int((time.monotonic() - self._start) * 1000)
        if exc_type is None:
            self._logger.info(
                f"{self._event}_completed",
                duration_ms=duration_ms,
                **self._extra,
            )
        else:
            self._logger.error(
                f"{self._event}_failed",
                duration_ms=duration_ms,
                error=str(exc_val),
                error_type=exc_type.__name__ if exc_type else None,
                **self._extra,
            )
