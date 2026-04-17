"""
logger.py
---------
Structured logging via structlog with Railway-compatible output.
- Logs are emitted as JSON to stdout for Railway's log drain.
- Secret values are NEVER passed to the logger.
- Call get_logger(__name__) in every module.
"""

import logging
import sys
import structlog


def configure_logging() -> None:
    """
    Set up structlog with JSON rendering.
    Uses the simpler PrintLoggerFactory approach which is compatible
    with structlog 21+ and outputs clean JSON to stdout.
    """
    # Determine log level from env (import inline to avoid circular deps)
    import os
    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    # Configure stdlib root logger (catches any non-structlog output)
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Configure structlog with a simple, reliable processor chain
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger for the given module name."""
    return structlog.get_logger(name)
