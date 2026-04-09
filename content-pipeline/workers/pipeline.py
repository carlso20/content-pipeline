"""
workers/pipeline.py
--------------------
Main pipeline orchestrator for Phase 2.

Called by the YouTube poller after each poll cycle. Picks up any episodes
sitting in an actionable status and routes them to the correct next worker.

The pipeline is resumable — it checks the current episode status and starts
from the next incomplete step rather than replaying from the beginning.

Status flow:
    classified → extracting_audio → transcribing → editing → publishing → completed

Each step is idempotent. Re-running the same episode_id will skip completed
steps and continue from where processing left off.
"""

import traceback
from datetime import datetime, timezone

from database import get_session
from logger import get_logger
from models.brand_config import BrandConfig
from models.dead_letter_queue import DeadLetterQueueItem
from models.episodes import Episode, EpisodeStatus

logger = get_logger(__name__)

# Statuses the pipeline will pick up and process
ACTIONABLE_STATUSES = [
    EpisodeStatus.CLASSIFIED,
    EpisodeStatus.EXTRACTING_AUDIO,
    EpisodeStatus.TRANSCRIBING,
    EpisodeStatus.EDITING,
    EpisodeStatus.PUBLISHING,
]

MAX_RETRIES = 3


def process_pending_episodes() -> None:
    """
    Query all episodes in actionable statuses and route each to its next worker.
    Called by the YouTube poller after every poll cycle.
    """
    with get_session() as session:
        pending = (
            session.query(Episode)
            .filter(Episode.status.in_(ACTIONABLE_STATUSES))
            .filter(Episode.retry_count < MAX_RETRIES)
            .all()
        )

    if not pending:
        return

    logger.info("Pipeline processing pending episodes", count=len(pending))

    for episode in pending:
        try:
            _process_episode(episode)
        except Exception as exc:
            logger.error(
                "Unhandled pipeline error",
                episode_id=str(episode.episode_id),
                youtube_video_id=episode.youtube_video_id,
                error=str(exc),
                traceback=traceback.format_exc(),
            )


def _process_episode(episode: Episode) -> None:
    """Route an episode to its next processing step based on current status."""
    # Reload brand config fresh for each episode
    with get_session() as session:
        ep = session.query(Episode).filter(
            Episode.episode_id == episode.episode_id
        ).first()
        if not ep:
            return
        brand = session.query(BrandConfig).filter(
            BrandConfig.id == ep.brand_config_id
        ).first()

    if not brand:
        logger.error("Brand config not found for episode", episode_id=str(ep.episode_id))
        return

    logger.info(
        "Processing episode",
        episode_id=str(ep.episode_id),
        youtube_video_id=ep.youtube_video_id,
        title=ep.title,
        status=ep.status,
        brand=brand.brand_name,
        content_path=ep.content_path,
    )

    try:
        if ep.status in (EpisodeStatus.CLASSIFIED, EpisodeStatus.EXTRACTING_AUDIO):
            _run_step(ep, EpisodeStatus.EXTRACTING_AUDIO, _step_extract_audio, brand)

        # Reload after each step so status is current
        ep = _reload(ep.episode_id)
        if ep.status == EpisodeStatus.TRANSCRIBING:
            _run_step(ep, EpisodeStatus.TRANSCRIBING, _step_transcribe, brand)

        ep = _reload(ep.episode_id)
        if ep.status == EpisodeStatus.EDITING:
            _run_step(ep, EpisodeStatus.EDITING, _step_edit_transcript, brand)

        ep = _reload(ep.episode_id)
        if ep.status == EpisodeStatus.PUBLISHING:
            _run_step(ep, EpisodeStatus.PUBLISHING, _step_publish, brand)

        ep = _reload(ep.episode_id)
        if ep.status == EpisodeStatus.PUBLISHING:
            # If still publishing after publish step, mark complete
            _set_status(ep.episode_id, EpisodeStatus.COMPLETED)
            logger.info(
                "Episode completed",
                episode_id=str(ep.episode_id),
                youtube_video_id=ep.youtube_video_id,
            )

    except Exception as exc:
        _handle_failure(ep, brand, str(exc), traceback.format_exc())


def _run_step(episode: Episode, step_status: EpisodeStatus, fn, brand: BrandConfig) -> None:
    """Set episode status, run the worker function, advance status on success."""
    _set_status(episode.episode_id, step_status)
    fn(episode, brand)


def _step_extract_audio(episode: Episode, brand: BrandConfig) -> None:
    from workers.audio_extractor import extract_audio
    audio_path, checksum, duration = extract_audio(episode)
    with get_session() as session:
        ep = session.query(Episode).filter(Episode.episode_id == episode.episode_id).first()
        ep.audio_file_path = audio_path
        ep.audio_checksum = checksum
        ep.audio_duration_seconds = duration
        ep.status = EpisodeStatus.TRANSCRIBING


def _step_transcribe(episode: Episode, brand: BrandConfig) -> None:
    from workers.transcriber import transcribe
    ep = _reload(episode.episode_id)
    transcribe(ep, brand)
    _set_status(episode.episode_id, EpisodeStatus.EDITING)


def _step_edit_transcript(episode: Episode, brand: BrandConfig) -> None:
    from workers.transcript_editor import edit_transcript
    ep = _reload(episode.episode_id)
    edit_transcript(ep, brand)
    _set_status(episode.episode_id, EpisodeStatus.PUBLISHING)


def _step_publish(episode: Episode, brand: BrandConfig) -> None:
    from workers.buzzsprout_publisher import publish_episode
    ep = _reload(episode.episode_id)
    publish_episode(ep, brand)
    _set_status(episode.episode_id, EpisodeStatus.COMPLETED)


def _handle_failure(episode: Episode, brand: BrandConfig, error: str, tb: str) -> None:
    """Increment retry count; move to dead letter queue after MAX_RETRIES."""
    with get_session() as session:
        ep = session.query(Episode).filter(Episode.episode_id == episode.episode_id).first()
        ep.retry_count += 1
        ep.last_error = error

        if ep.retry_count >= MAX_RETRIES:
            ep.status = EpisodeStatus.FAILED
            logger.error(
                "Episode moved to failed — max retries exhausted",
                episode_id=str(ep.episode_id),
                youtube_video_id=ep.youtube_video_id,
                retry_count=ep.retry_count,
            )
            # Write to dead letter queue
            dlq = DeadLetterQueueItem(
                episode_id=ep.episode_id,
                youtube_video_id=ep.youtube_video_id,
                brand_name=brand.brand_name,
                failed_step=str(ep.status),
                error_message=error,
                error_traceback=tb,
                retry_count=ep.retry_count,
            )
            session.add(dlq)

            # Notify Carl
            try:
                from workers.notifier import send_failure_alert
                send_failure_alert(ep, brand, error)
            except Exception as notify_exc:
                logger.error("Failed to send failure notification", error=str(notify_exc))
        else:
            # Will be retried on next poll cycle
            ep.status = EpisodeStatus.CLASSIFIED
            logger.warning(
                "Episode will be retried",
                episode_id=str(ep.episode_id),
                retry_count=ep.retry_count,
                max_retries=MAX_RETRIES,
            )


def _set_status(episode_id, status: EpisodeStatus) -> None:
    with get_session() as session:
        ep = session.query(Episode).filter(Episode.episode_id == episode_id).first()
        if ep:
            ep.status = status
            ep.updated_at = datetime.now(timezone.utc)


def _reload(episode_id) -> Episode:
    with get_session() as session:
        return session.query(Episode).filter(Episode.episode_id == episode_id).first()
