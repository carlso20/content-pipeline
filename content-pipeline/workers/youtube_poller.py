"""
workers/youtube_poller.py
--------------------------
YouTube upload detection worker. Runs every POLL_INTERVAL_MINUTES minutes
via APScheduler.

Poll cycle per brand:
  1. Load active brands from brand_config
  2. Query YouTube Data API for recent uploads on each channel
  3. Compare video IDs against sync_state (known seen IDs)
  4. For each new video: create an episodes record and kick off path detection
  5. Update sync_state with last_polled_at and last successful video ID

Idempotency: youtube_video_id is the canonical external key.
Inserting the same ID twice will hit the unique constraint and skip silently.

Quota: at 50 units per channels.list + 1 unit per search.list call,
polling 3 channels every 10 minutes consumes ~216 units/hour, well within
the 10,000 daily unit quota.
"""

import re
import traceback
from datetime import datetime, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import Config
from database import get_session
from logger import get_logger
from models.brand_config import BrandConfig
from models.episodes import ContentPath, Episode, EpisodeStatus
from models.sync_state import SyncState

logger = get_logger(__name__)

# Tags that Carl adds to YouTube descriptions to declare the content path
PATH_A_TAGS = {"#patha", "#blogexists", "#path_a"}
PATH_B_TAGS = {"#pathb", "#noblog", "#path_b"}

# YouTube Data API search.list returns max 50 results; we only need recent ones
MAX_RESULTS_PER_POLL = 10


class YouTubePoller:
    """
    Encapsulates the YouTube polling logic.
    Instantiated once at startup; poll() is called by APScheduler.
    """

    def __init__(self, app=None):
        self.app = app  # Flask app (for app context if needed later)
        self._youtube_client = None

    def _get_youtube_client(self):
        """Lazy-initialize the YouTube API client."""
        if self._youtube_client is None:
            if not Config.YOUTUBE_API_KEY:
                raise RuntimeError(
                    "YOUTUBE_API_KEY is not set. Cannot poll YouTube."
                )
            self._youtube_client = build(
                "youtube", "v3", developerKey=Config.YOUTUBE_API_KEY
            )
        return self._youtube_client

    def poll(self) -> None:
        """
        Main polling entry point. Called by APScheduler on the configured interval.
        Catches all exceptions to prevent the scheduler job from dying.
        """
        logger.info("YouTube poll cycle started")
        try:
            with get_session() as session:
                active_brands = (
                    session.query(BrandConfig)
                    .filter(BrandConfig.is_active == True)  # noqa: E712
                    .all()
                )

            if not active_brands:
                logger.warning("No active brands found in brand_config — skipping poll")
                return

            for brand in active_brands:
                try:
                    self._poll_brand(brand)
                except Exception as exc:
                    logger.error(
                        "Brand poll failed",
                        brand=brand.brand_name,
                        error=str(exc),
                        traceback=traceback.format_exc(),
                    )

        except Exception as exc:
            logger.error(
                "Poll cycle failed",
                error=str(exc),
                traceback=traceback.format_exc(),
            )

        logger.info("YouTube poll cycle complete")

        # Trigger pipeline for any episodes awaiting processing
        try:
            from workers.pipeline import process_pending_episodes
            process_pending_episodes()
        except Exception as exc:
            logger.error(
                "Pipeline processing failed",
                error=str(exc),
                traceback=traceback.format_exc(),
            )

    def _poll_brand(self, brand: BrandConfig) -> None:
        """Poll a single brand channel for new uploads."""
        if not brand.youtube_channel_id or brand.youtube_channel_id.startswith("UC_TODO"):
            logger.warning(
                "Brand has no resolved YouTube channel ID — skipping",
                brand=brand.brand_name,
                channel_id=brand.youtube_channel_id,
            )
            return

        logger.info(
            "Polling brand channel",
            brand=brand.brand_name,
            channel_id=brand.youtube_channel_id,
        )

        # Fetch recent uploads from YouTube
        try:
            videos = self._fetch_recent_uploads(brand.youtube_channel_id)
        except Exception as exc:
            logger.error(
                "YouTube API call failed",
                brand=brand.brand_name,
                channel_id=brand.youtube_channel_id,
                error=str(exc),
            )
            self._increment_consecutive_failures(brand.id)
            return

        # Update poll timestamp regardless of new videos found
        self._update_last_polled_at(brand.id)

        if not videos:
            logger.info("No recent uploads found", brand=brand.brand_name)
            return

        new_count = 0
        for video in videos:
            video_id = video["id"]["videoId"]
            created = self._create_episode_if_new(brand, video)
            if created:
                new_count += 1

        logger.info(
            "Brand poll complete",
            brand=brand.brand_name,
            new_episodes=new_count,
            videos_checked=len(videos),
        )

        # Reset failure counter on success
        self._reset_consecutive_failures(brand.id)

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _fetch_recent_uploads(self, channel_id: str) -> list[dict]:
        """
        Call YouTube Data API search.list to get recent video uploads.
        Retries on HttpError with exponential backoff (max 3 attempts).
        """
        youtube = self._get_youtube_client()
        request = youtube.search().list(
            part="id,snippet",
            channelId=channel_id,
            type="video",
            order="date",
            maxResults=MAX_RESULTS_PER_POLL,
        )
        response = request.execute()
        items = response.get("items", [])
        # Filter to videoId results only (search can return channels/playlists too)
        return [item for item in items if item.get("id", {}).get("kind") == "youtube#video"]

    def _create_episode_if_new(self, brand: BrandConfig, video: dict) -> bool:
        """
        Create an Episode record if the youtube_video_id hasn't been seen before.
        Returns True if a new record was created, False if it already existed.
        """
        video_id = video["id"]["videoId"]
        snippet = video.get("snippet", {})
        title = snippet.get("title", "")
        description = snippet.get("description", "")
        published_at_str = snippet.get("publishedAt")
        thumbnail_url = (
            snippet.get("thumbnails", {}).get("high", {}).get("url")
            or snippet.get("thumbnails", {}).get("default", {}).get("url")
        )

        with get_session() as session:
            # Idempotency check — skip if already recorded
            existing = (
                session.query(Episode)
                .filter(Episode.youtube_video_id == video_id)
                .first()
            )
            if existing:
                return False

            # Detect content path from description tags
            content_path, detection_method = self._detect_content_path(description)

            # Parse published_at
            published_at = None
            if published_at_str:
                try:
                    published_at = datetime.fromisoformat(
                        published_at_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass

            # Create the episode record
            episode = Episode(
                youtube_video_id=video_id,
                brand_config_id=brand.id,
                title=title,
                description=description,
                youtube_published_at=published_at,
                youtube_url=f"https://www.youtube.com/watch?v={video_id}",
                thumbnail_url=thumbnail_url,
                content_path=content_path,
                path_detection_method=detection_method,
                status=(
                    EpisodeStatus.CLASSIFIED
                    if content_path != ContentPath.UNKNOWN
                    else EpisodeStatus.NEEDS_CLASSIFICATION
                ),
            )
            session.add(episode)

        logger.info(
            "New episode detected",
            brand=brand.brand_name,
            youtube_video_id=video_id,
            title=title,
            content_path=content_path,
            detection_method=detection_method,
        )

        if content_path == ContentPath.UNKNOWN:
            logger.warning(
                "Episode needs manual classification — pipeline halted",
                youtube_video_id=video_id,
                title=title,
            )
            self._create_classification_task(brand, video_id, title)

        return True

    def _detect_content_path(
        self, description: str
    ) -> tuple[ContentPath, str]:
        """
        Deterministic content path detection.
        Hierarchy:
          1. Explicit YouTube description tag (#PathA / #PathB variants)
          2. (Phase 1 placeholder) ClickUp lookup — not yet implemented
          3. UNKNOWN → needs_classification

        Returns (ContentPath, detection_method_string).
        """
        if not description:
            return ContentPath.UNKNOWN, "no_description"

        # Normalize: lowercase, strip whitespace
        normalized = description.lower()

        # Check for Path A tags
        for tag in PATH_A_TAGS:
            if tag in normalized:
                return ContentPath.PATH_A, "youtube_tag"

        # Check for Path B tags
        for tag in PATH_B_TAGS:
            if tag in normalized:
                return ContentPath.PATH_B, "youtube_tag"

        # Phase 2+: ClickUp lookup fallback goes here
        # For now, untagged episodes require manual classification
        return ContentPath.UNKNOWN, "no_tag_found"

    def _create_classification_task(
        self, brand: BrandConfig, video_id: str, title: str
    ) -> None:
        """
        Creates a ClickUp task asking Carl to classify the episode as Path A or B.
        Phase 1 stub — ClickUp integration is wired in Phase 2.
        """
        logger.info(
            "Classification task needed (ClickUp integration pending Phase 2)",
            brand=brand.brand_name,
            youtube_video_id=video_id,
            title=title,
        )
        # TODO (Phase 2): POST to ClickUp API, list=CLICKUP_APPROVAL_QUEUE_LIST_ID
        # Task body should include:
        #   - YouTube URL
        #   - Episode title
        #   - Brand name
        #   - Instructions: add #PathA or #PathB to YouTube description, then requeue

    def _update_last_polled_at(self, brand_config_id: int) -> None:
        """Update or create the sync_state row for this brand."""
        now = datetime.now(timezone.utc)
        with get_session() as session:
            state = (
                session.query(SyncState)
                .filter(SyncState.brand_config_id == brand_config_id)
                .first()
            )
            if state:
                state.last_polled_at = now
                state.last_successful_poll_at = now
            else:
                state = SyncState(
                    brand_config_id=brand_config_id,
                    last_polled_at=now,
                    last_successful_poll_at=now,
                )
                session.add(state)

    def _increment_consecutive_failures(self, brand_config_id: int) -> None:
        with get_session() as session:
            state = (
                session.query(SyncState)
                .filter(SyncState.brand_config_id == brand_config_id)
                .first()
            )
            if state:
                state.consecutive_failures += 1
                state.last_polled_at = datetime.now(timezone.utc)
            else:
                state = SyncState(
                    brand_config_id=brand_config_id,
                    consecutive_failures=1,
                    last_polled_at=datetime.now(timezone.utc),
                )
                session.add(state)

    def _reset_consecutive_failures(self, brand_config_id: int) -> None:
        with get_session() as session:
            state = (
                session.query(SyncState)
                .filter(SyncState.brand_config_id == brand_config_id)
                .first()
            )
            if state:
                state.consecutive_failures = 0
