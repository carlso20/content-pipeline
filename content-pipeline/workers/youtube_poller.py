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

# Minimum video duration in seconds to be considered a full episode (not a short/clip)
MIN_EPISODE_DURATION_SECONDS = 7 * 60  # 7 minutes


def _parse_iso8601_duration(duration_str: str) -> int:
    """
    Parse an ISO 8601 duration string (e.g. "PT7M30S", "PT1H2M3S") into seconds.
    No external library needed — covers the subset YouTube returns.
    Returns 0 if the string can't be parsed.
    """
    if not duration_str:
        return 0
    pattern = re.compile(
        r"P(?:(?P<days>\d+)D)?"
        r"(?:T"
        r"(?:(?P<hours>\d+)H)?"
        r"(?:(?P<minutes>\d+)M)?"
        r"(?:(?P<seconds>\d+)S)?"
        r")?"
    )
    m = pattern.match(duration_str)
    if not m:
        return 0
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    minutes = int(m.group("minutes") or 0)
    seconds = int(m.group("seconds") or 0)
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


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
            # Extract brand IDs only — objects detach when session closes
            with get_session() as session:
                brand_ids = [
                    b.id for b in
                    session.query(BrandConfig)
                    .filter(BrandConfig.is_active == True)  # noqa: E712
                    .all()
                ]

            if not brand_ids:
                logger.warning("No active brands found in brand_config — skipping poll")
                return

            # Reload each brand in its own session so attributes are always fresh
            for brand_id in brand_ids:
                with get_session() as session:
                    brand = session.query(BrandConfig).filter(
                        BrandConfig.id == brand_id
                    ).first()
                    if not brand:
                        continue
                    brand_name = brand.brand_name
                    channel_id = brand.youtube_channel_id

                try:
                    self._poll_brand(brand_id)
                except Exception as exc:
                    logger.error(
                        "Brand poll failed",
                        brand=brand_name,
                        channel_id=channel_id,
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

    def _poll_brand(self, brand_id: int) -> None:
        """Poll a single brand channel for new uploads. Reloads brand fresh from DB."""
        # Load brand fresh in its own session to avoid DetachedInstanceError
        with get_session() as session:
            brand = session.query(BrandConfig).filter(BrandConfig.id == brand_id).first()
            if not brand:
                logger.warning("Brand not found", brand_id=brand_id)
                return
            # Capture scalar values while session is open
            brand_name = brand.brand_name
            channel_id = brand.youtube_channel_id
            brand_id_val = brand.id

        if not channel_id or channel_id.startswith("UC_TODO"):
            logger.warning(
                "Brand has no resolved YouTube channel ID — skipping",
                brand=brand_name,
                channel_id=channel_id,
            )
            return

        logger.info("Polling brand channel", brand=brand_name, channel_id=channel_id)

        # Fetch recent uploads from YouTube
        try:
            videos = self._fetch_recent_uploads(channel_id)
        except Exception as exc:
            logger.error(
                "YouTube API call failed",
                brand=brand_name,
                channel_id=channel_id,
                error=str(exc),
            )
            self._increment_consecutive_failures(brand_id_val)
            return

        # Update poll timestamp regardless of new videos found
        self._update_last_polled_at(brand_id_val)

        if not videos:
            logger.info("No recent uploads found", brand=brand_name)
            return

        new_count = 0
        for video in videos:
            # Reload brand again for episode creation (needs full object)
            with get_session() as session:
                brand = session.query(BrandConfig).filter(
                    BrandConfig.id == brand_id_val
                ).first()
                created = self._create_episode_if_new(brand, video)
            if created:
                new_count += 1

        logger.info(
            "Brand poll complete",
            brand=brand_name,
            new_episodes=new_count,
            videos_checked=len(videos),
        )

        # Reset failure counter on success
        self._reset_consecutive_failures(brand_id_val)

    @retry(
        retry=retry_if_exception_type(HttpError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _fetch_recent_uploads(self, channel_id: str) -> list[dict]:
        """
        Fetch recent public videos from a channel that are longer than
        MIN_EPISODE_DURATION_SECONDS (default 7 minutes).

        Two-pass approach:
          1. search.list — get the most recent video candidates (snippet only)
          2. videos.list — enrich with contentDetails (duration) and status
             (privacyStatus), then filter to public full-length episodes only.

        This costs 100 + 1 quota units per call (search.list=100, videos.list=1).
        At 3 brands every 10 minutes that's ~303 units/hour, well within the
        10,000 daily quota.
        """
        youtube = self._get_youtube_client()

        # Step 1: Get recent video IDs and snippets via search
        # Use videoDuration=medium|long to pre-filter at the API level and
        # reduce the videos.list batch size. "medium" = 4–20 min, "long" = >20 min.
        # We fetch both since our 7-min threshold sits in the medium range.
        candidates = []
        for duration_filter in ("medium", "long"):
            response = youtube.search().list(
                part="id,snippet",
                channelId=channel_id,
                type="video",
                order="date",
                videoDuration=duration_filter,
                videoEmbeddable="true",
                maxResults=MAX_RESULTS_PER_POLL,
            ).execute()
            items = response.get("items", [])
            candidates.extend(
                item for item in items
                if item.get("id", {}).get("kind") == "youtube#video"
            )

        if not candidates:
            return []

        # Deduplicate by video ID (medium/long ranges can overlap at boundaries)
        seen_ids = set()
        unique_candidates = []
        for item in candidates:
            vid = item["id"]["videoId"]
            if vid not in seen_ids:
                seen_ids.add(vid)
                unique_candidates.append(item)

        # Step 2: Enrich with duration and privacy status via videos.list
        video_ids = [item["id"]["videoId"] for item in unique_candidates]
        enriched = self._enrich_video_metadata(video_ids)

        # Step 3: Filter — must be public and >= MIN_EPISODE_DURATION_SECONDS
        filtered = []
        for item in unique_candidates:
            vid = item["id"]["videoId"]
            meta = enriched.get(vid, {})
            duration_sec = meta.get("duration_seconds", 0)
            privacy = meta.get("privacy_status", "")
            is_public = privacy == "public"
            is_long_enough = duration_sec >= MIN_EPISODE_DURATION_SECONDS

            if is_public and is_long_enough:
                # Attach duration to the item so _create_episode_if_new can store it
                item["_duration_seconds"] = duration_sec
                filtered.append(item)
            else:
                logger.debug(
                    "Video filtered out",
                    video_id=vid,
                    title=item.get("snippet", {}).get("title", "")[:60],
                    duration_seconds=duration_sec,
                    privacy_status=privacy,
                    reason="too_short" if not is_long_enough else "not_public",
                )

        return filtered

    def _enrich_video_metadata(self, video_ids: list[str]) -> dict[str, dict]:
        """
        Call videos.list to get duration and privacy status for a batch of video IDs.
        Returns a dict keyed by video_id:
          { "video_id": { "duration_seconds": int, "privacy_status": str } }

        Handles batches of up to 50 IDs per call (YouTube API limit).
        """
        youtube = self._get_youtube_client()
        result = {}

        # Batch in chunks of 50
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i + 50]
            response = youtube.videos().list(
                part="contentDetails,status",
                id=",".join(batch),
            ).execute()

            for item in response.get("items", []):
                vid = item["id"]
                duration_str = item.get("contentDetails", {}).get("duration", "PT0S")
                privacy = item.get("status", {}).get("privacyStatus", "unknown")

                try:
                    duration_sec = _parse_iso8601_duration(duration_str)
                except Exception:
                    duration_sec = 0

                result[vid] = {
                    "duration_seconds": duration_sec,
                    "privacy_status": privacy,
                }

        return result

    def _create_episode_if_new(self, brand: BrandConfig, video: dict) -> bool:
        """
        Create an Episode record if the youtube_video_id hasn't been seen before
        AND no episode with the same title already exists for this brand.

        The title deduplication guard handles YouTube Podcast syndicated copies:
        when YouTube auto-creates a podcast episode for a regular video upload,
        both share the same title but have different video IDs. We keep whichever
        was seen first and skip the duplicate.

        Returns True if a new record was created, False if skipped.
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
        duration_seconds = video.get("_duration_seconds")

        with get_session() as session:
            # Idempotency check — skip if this video ID was already recorded
            existing_by_id = (
                session.query(Episode)
                .filter(Episode.youtube_video_id == video_id)
                .first()
            )
            if existing_by_id:
                return False

            # Title deduplication — skip YouTube Podcast syndicated copies
            # A syndicated copy has the same title as an existing episode for
            # this brand but a different video ID.
            if title:
                existing_by_title = (
                    session.query(Episode)
                    .filter(
                        Episode.brand_config_id == brand.id,
                        Episode.title == title,
                    )
                    .first()
                )
                if existing_by_title:
                    logger.info(
                        "Skipping duplicate title — likely YouTube Podcast syndication",
                        brand=brand.brand_name,
                        youtube_video_id=video_id,
                        title=title,
                        existing_video_id=existing_by_title.youtube_video_id,
                    )
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
                duration_seconds=duration_seconds,
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
            duration_seconds=duration_seconds,
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
