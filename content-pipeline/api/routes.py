"""
api/routes.py
-------------
Flask HTTP endpoints for operational visibility and liveness checks.

GET /health  — Railway liveness probe. Returns 200 if the service is running.
GET /status  — Operational dashboard: dark-launch state, last poll time,
               episode queue health, brand config summary.
"""

from datetime import datetime, timezone

from flask import Flask, jsonify

from config import Config
from database import get_session
from logger import get_logger
from models.brand_config import BrandConfig
from models.dead_letter_queue import DeadLetterQueueItem
from models.episodes import Episode, EpisodeStatus
from models.sync_state import SyncState

logger = get_logger(__name__)

# Module-level reference to the APScheduler instance (set by main.py after start)
_scheduler = None


def set_scheduler(scheduler) -> None:
    global _scheduler
    _scheduler = scheduler


def register_routes(app: Flask) -> None:
    """Register all HTTP routes on the Flask app."""

    @app.get("/health")
    def health():
        """
        Liveness endpoint for Railway's health check.
        Returns 200 OK if the service process is alive.
        A database connectivity check is intentionally lightweight here;
        deeper checks live in /status.
        """
        return jsonify({"status": "ok", "timestamp": _utc_now()}), 200

    @app.get("/status")
    def status():
        """
        Operational status dashboard.
        Includes: dark launch state, scheduler info, queue health, brand sync state.
        """
        try:
            payload = {
                "timestamp": _utc_now(),
                "service": "content-pipeline",
                "environment": Config.ENVIRONMENT,
                "dark_launch_mode": Config.DARK_LAUNCH_MODE,
                "poll_interval_minutes": Config.POLL_INTERVAL_MINUTES,
                "scheduler": _scheduler_status(),
                "queue": _queue_health(),
                "brands": _brand_sync_status(),
            }
            return jsonify(payload), 200
        except Exception as exc:
            logger.error("Status endpoint failed", error=str(exc))
            return jsonify({"status": "error", "error": str(exc)}), 500

    @app.get("/status/episodes")
    def episode_status():
        """
        Returns recent episode counts by status.
        Useful for monitoring pipeline throughput.
        """
        try:
            with get_session() as session:
                counts = {}
                for status_val in EpisodeStatus:
                    count = (
                        session.query(Episode)
                        .filter(Episode.status == status_val)
                        .count()
                    )
                    counts[status_val.value] = count

            return jsonify({"episode_counts_by_status": counts}), 200
        except Exception as exc:
            logger.error("Episode status endpoint failed", error=str(exc))
            return jsonify({"error": str(exc)}), 500


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scheduler_status() -> dict:
    """Return scheduler metadata if available."""
    if _scheduler is None:
        return {"running": False, "note": "Scheduler not initialized"}

    jobs = []
    for job in _scheduler.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "id": job.id,
            "next_run": next_run.isoformat() if next_run else None,
        })

    return {
        "running": _scheduler.running,
        "jobs": jobs,
    }


def _queue_health() -> dict:
    """Return pipeline queue health metrics."""
    try:
        with get_session() as session:
            needs_classification = (
                session.query(Episode)
                .filter(Episode.status == EpisodeStatus.NEEDS_CLASSIFICATION)
                .count()
            )
            in_progress = (
                session.query(Episode)
                .filter(
                    Episode.status.in_([
                        EpisodeStatus.CLASSIFYING,
                        EpisodeStatus.CLASSIFIED,
                        EpisodeStatus.EXTRACTING_AUDIO,
                        EpisodeStatus.TRANSCRIBING,
                        EpisodeStatus.EDITING,
                        EpisodeStatus.PUBLISHING,
                        EpisodeStatus.REPURPOSING,
                    ])
                )
                .count()
            )
            failed = (
                session.query(Episode)
                .filter(Episode.status == EpisodeStatus.FAILED)
                .count()
            )
            dlq_unresolved = (
                session.query(DeadLetterQueueItem)
                .filter(DeadLetterQueueItem.resolved == False)  # noqa: E712
                .count()
            )

        return {
            "needs_classification": needs_classification,
            "in_progress": in_progress,
            "failed": failed,
            "dead_letter_queue_unresolved": dlq_unresolved,
        }
    except Exception as exc:
        return {"error": str(exc)}


def _brand_sync_status() -> list[dict]:
    """Return last poll time and consecutive failures per brand."""
    try:
        with get_session() as session:
            brands = session.query(BrandConfig).filter(BrandConfig.is_active == True).all()  # noqa: E712
            result = []
            for brand in brands:
                sync = (
                    session.query(SyncState)
                    .filter(SyncState.brand_config_id == brand.id)
                    .first()
                )
                result.append({
                    "brand": brand.brand_name,
                    "youtube_channel_id": brand.youtube_channel_id,
                    "last_polled_at": sync.last_polled_at.isoformat() if sync and sync.last_polled_at else None,
                    "last_successful_poll_at": sync.last_successful_poll_at.isoformat() if sync and sync.last_successful_poll_at else None,
                    "consecutive_failures": sync.consecutive_failures if sync else 0,
                })
        return result
    except Exception as exc:
        return [{"error": str(exc)}]
