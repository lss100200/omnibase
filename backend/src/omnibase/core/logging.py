"""Structured logging configuration (structlog).

Two rendering modes:
- development: pretty colored console output
- production: JSON for log aggregation (ELK / Loki / CloudWatch)

Usage:
    from omnibase.core.logging import get_logger
    log = get_logger(__name__)
    log.info("user_registered", user_id=..., tenant_id=...)
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from omnibase.core.config import LogLevel, get_settings


def _add_app_context(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
    """Inject app-wide fields into every log event."""
    settings = get_settings()
    event_dict.setdefault("app", settings.app_name)
    event_dict.setdefault("env", settings.env.value)
    return event_dict


def _drop_debug_loggers(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
    """Silence known chatty libraries in production."""
    return event_dict


def configure_logging() -> None:
    """Configure root + structlog logging.

    Idempotent: safe to call multiple times (e.g. on settings reload).
    """
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.value, logging.INFO)

    # -------------------------------------------------------
    # Standard library logging (for 3rd-party libs: uvicorn, sqlalchemy)
    # -------------------------------------------------------
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
        force=True,
    )

    # Tame noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # SQLAlchemy INFO logs include statements, tenant schema/physical names,
    # and bind values. Keep them disabled by default in every environment;
    # temporary SQL tracing must be an explicit operator action and must never
    # be enabled in shared evidence or normal application logs.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # -------------------------------------------------------
    # structlog processor chain
    # -------------------------------------------------------
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_app_context,
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.is_production:
        # Machine-readable tracebacks for log aggregation. Exception rendering
        # processors are mutually exclusive, so production uses only this one.
        shared_processors.append(structlog.processors.dict_tracebacks)
        renderer: Processor = structlog.processors.JSONRenderer(serializer=_json_serializer)
    else:
        # ConsoleRenderer formats exc_info itself; adding another exception
        # renderer here creates duplicate processing and runtime warnings.
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def _json_serializer(obj: Any, default: Any = None, **kwargs: Any) -> str:
    """JSON serializer that handles non-JSON-serializable objects (e.g. UUID, datetime)."""
    import json
    from datetime import date, datetime
    from decimal import Decimal
    from enum import Enum
    from pathlib import Path
    from uuid import UUID

    def _fallback(o: Any) -> Any:
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, Enum):
            return o.value
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, BaseException):
            return {"type": type(o).__name__, "message": str(o)}
        return str(o)

    return json.dumps(obj, default=_fallback, ensure_ascii=False, **kwargs)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a structured logger bound to the given module name.

    Args:
        name: Module name (typically `__name__`). Pass None for root logger.

    Returns:
        A bound structlog logger.
    """
    return structlog.get_logger(name)


__all__ = ["LogLevel", "configure_logging", "get_logger"]
