"""
logger.py
---------
Structured JSON logging via structlog.
- All log events are emitted as JSON for Railway's log drain.
- Secret values are NEVER passed to the logger; use config.redacted_summary().
- Call get_logger(__name__) in every module.
"""

import logging
import sys
import structlog
from config import Config


def configure_logging() -> None:
    """
    Set up structlog with JSON rendering and stdlib integration.
    Call once at application startup (main.py).
    """
    log_level = getattr(logging, Config.LOG_LEVEL, logging.INFO)

    # Configure stdlib logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Processors applied to every log event
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Attach JSON formatter to root handler
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )
    root_logger = logging.getLogger()
    if root_logger.handlers:
        root_logger.handlers[0].setFormatter(formatter)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound logger for the given module name."""
    return structlog.get_logger(name)
