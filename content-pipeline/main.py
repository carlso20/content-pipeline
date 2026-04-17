"""
main.py
-------
Entry point for the Content Pipeline orchestrator.

Startup sequence:
  1. Configure structured logging
  2. Validate required environment variables
  3. Initialize database (create tables if not exist)
  4. Create Flask app and register routes
  5. Start APScheduler with the YouTube polling job
  6. Bind Flask to PORT (Railway injects this)

The scheduler runs in a background thread. Flask serves /health and /status
on the main thread.
"""

import os
import signal
import sys

# Plain print before any imports so Railway logs show something even if
# a dependency import crashes below this line
print("=== Content Pipeline starting ===", flush=True)

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

from api.routes import register_routes, set_scheduler
from config import Config
from database import init_db
from logger import configure_logging, get_logger
from workers.youtube_poller import YouTubePoller

# Configure logging first so all subsequent imports can log
configure_logging()
logger = get_logger(__name__)


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__)
    register_routes(app)
    return app


def create_scheduler(app: Flask) -> BackgroundScheduler:
    """
    Create and start the APScheduler background scheduler.
    Registers the YouTube polling job on the configured interval.
    """
    scheduler = BackgroundScheduler(timezone="UTC")
    poller = YouTubePoller(app=app)

    scheduler.add_job(
        func=poller.poll,
        trigger="interval",
        minutes=Config.POLL_INTERVAL_MINUTES,
        id="youtube_poll",
        name="YouTube Upload Detector",
        replace_existing=True,
        max_instances=1,         # Prevent overlapping poll cycles
    )

    scheduler.start()

    logger.info(
        "Scheduler started",
        job_id="youtube_poll",
        poll_interval_minutes=Config.POLL_INTERVAL_MINUTES,
    )
    return scheduler


def main() -> None:
    logger.info("Content Pipeline starting up", config=Config.redacted_summary())

    # --- Validate required env vars ---
    missing = Config.validate_phase1()
    if missing:
        logger.warning(
            "Missing environment variables for Phase 1",
            missing=missing,
            hint="Set these in Railway Variables or .env before deploying",
        )
        if Config.is_production():
            logger.error("Refusing to start in production with missing required vars")
            sys.exit(1)

    # --- Initialize database ---
    try:
        init_db()
    except Exception as exc:
        logger.error("Database initialization failed", error=str(exc))
        sys.exit(1)

    # --- Create Flask app ---
    app = create_app()

    # --- Start scheduler ---
    scheduler = None
    if Config.YOUTUBE_API_KEY:
        scheduler = create_scheduler(app)
        set_scheduler(scheduler)
    else:
        logger.warning(
            "YOUTUBE_API_KEY not set — scheduler not started. "
            "Set the key and restart to enable polling."
        )

    # --- Graceful shutdown handler ---
    def shutdown(signum, frame):
        logger.info("Shutdown signal received, stopping scheduler...")
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)
        logger.info("Shutdown complete")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # --- Start Flask ---
    port = Config.PORT
    logger.info(
        "Flask server starting",
        port=port,
        dark_launch_mode=Config.DARK_LAUNCH_MODE,
        environment=Config.ENVIRONMENT,
    )

    # Run a single poll immediately on startup (before waiting for the interval)
    if scheduler and Config.YOUTUBE_API_KEY:
        logger.info("Running initial poll on startup...")
        try:
            YouTubePoller(app=app).poll()
        except Exception as exc:
            logger.warning("Initial poll failed (non-fatal)", error=str(exc))

    app.run(
        host="0.0.0.0",
        port=port,
        debug=(Config.ENVIRONMENT == "development"),
        use_reloader=False,   # Disabled: APScheduler conflicts with Flask reloader
    )


if __name__ == "__main__":
    main()
