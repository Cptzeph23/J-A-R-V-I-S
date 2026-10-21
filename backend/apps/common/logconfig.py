"""Structured logging with automatic redaction of sensitive fields."""

from __future__ import annotations

import re
from typing import Any

import structlog
from structlog.typing import EventDict, Processor

REDACTED = "[REDACTED]"
MAX_SCRUB_DEPTH = 6

# A key is redacted when its lower-cased name contains any of these fragments.
SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "cookie",
    "credential",
    "private_key",
    "session",
    "otp",
)
_AUTH_VALUE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")


def _is_sensitive_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def scrub_value(value: Any, depth: int = 0) -> Any:
    """Return a copy of value with sensitive keys and bearer/basic credentials redacted."""
    if depth > MAX_SCRUB_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        return {
            k: REDACTED if _is_sensitive_key(k) else scrub_value(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, list | tuple | set):
        return [scrub_value(v, depth + 1) for v in value]
    if isinstance(value, str):
        return _AUTH_VALUE.sub(lambda m: f"{m.group(1)} {REDACTED}", value)
    return value


def scrub_processor(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    scrubbed: EventDict = scrub_value(dict(event_dict))
    return scrubbed


def _shared_processors() -> list[Processor]:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        scrub_processor,
    ]


def configure_structlog() -> None:
    """Route structlog through stdlib logging so Django and app logs share one format."""
    structlog.configure(
        processors=[*_shared_processors(), structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def build_logging_config(*, json_logs: bool, level: str) -> dict[str, Any]:
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if json_logs
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    processors: list[Processor] = [structlog.stdlib.ProcessorFormatter.remove_processors_meta]
    if json_logs:
        processors.append(structlog.processors.format_exc_info)
    processors.append(renderer)
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured": {
                "()": structlog.stdlib.ProcessorFormatter,
                "foreign_pre_chain": _shared_processors(),
                "processors": processors,
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "structured",
            }
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            "django.server": {"handlers": [], "level": "WARNING", "propagate": True},
        },
    }
