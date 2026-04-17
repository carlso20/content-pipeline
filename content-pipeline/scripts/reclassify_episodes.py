"""
scripts/reclassify_episodes.py
-------------------------------
One-shot script to reclassify episodes stuck in needs_classification status.

For each needs_classification episode, the script:
  1. Fetches the current YouTube description via videos.list (1 unit/batch)
  2. Runs the same tag detection logic as the poller (#PathA / #PathB)
  3. If a tag is found: updates content_path, path_detection_method, status → classified
  4. If no tag is found: reports which videos still need tagging and skips them

This script is safe to run multiple times — it only updates episodes where a
tag is now present and skips any that are already classified.

Usage (from the content-pipeline directory):
    python scripts/reclassify_episodes.py

    # Dry-run mode — shows what would change without writing to DB:
    python scripts/reclassify_episodes.py --dry-run
"""

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from googleapiclient.discovery import build

from config import Config
from database import init_db, get_session
from logger import configure_logging, get_logger
from models.episodes import ContentPath, Episode, EpisodeStatus

configure_logging()
logger = get_logger(__name__)

# Must match the sets in youtube_poller.py
PATH_A_TAGS = {"#patha", "#blogexists", "#path_a"}
PATH_B_TAGS = {"#pathb", "#noblog", "#path_b"}


def detect_path(description: str) -> tuple:
    """
    Run tag detection against a description string.
    Returns (ContentPath, detection_method) if a tag is found, else (None, None).
    """
    if not description:
        return None, None
    normalized = description.lower()
    for tag in PATH_A_TAGS:
        if tag in normalized:
            return ContentPath.PATH_A, "youtube_tag"
    for tag in PATH_B_TAGS:
        if tag in normalized:
            return ContentPath.PATH_B, "youtube_tag"
    return None, None


def fetch_descriptions(youtube_client, video_ids: list[str]) -> dict[str, str]:
    """
    Fetch current video descriptions from YouTube via videos.list.
    Batches up to 50 IDs per call.
    Returns { video_id: description_string }.
    """
    result = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        response = youtube_client.videos().list(
            part="snippet",
            id=",".join(batch),
        ).execute()
        for item in response.get("items", []):
            vid = item["id"]
            desc = item.get("snippet", {}).get("description", "") or ""
            result[vid] = desc
    return result


def reclassify(dry_run: bool = False) -> None:
    init_db()

    if not Config.YOUTUBE_API_KEY:
        logger.error("YOUTUBE_API_KEY is not set — cannot fetch descriptions from YouTube.")
        sys.exit(1)

    youtube = build("youtube", "v3", developerKey=Config.YOUTUBE_API_KEY)

    # Load all needs_classification episodes
    with get_session() as session:
        episodes = (
            session.query(Episode)
            .filter(Episode.status == EpisodeStatus.NEEDS_CLASSIFICATION)
            .all()
        )
        # Capture scalars while session is open
        episode_data = [
            {
                "episode_id": e.episode_id,
                "youtube_video_id": e.youtube_video_id,
                "title": e.title,
                "brand_config_id": e.brand_config_id,
            }
            for e in episodes
        ]

    if not episode_data:
        logger.info("No episodes in needs_classification — nothing to do.")
        return

    logger.info(
        "Found episodes awaiting classification",
        count=len(episode_data),
        video_ids=[e["youtube_video_id"] for e in episode_data],
    )

    # Fetch current descriptions from YouTube in one batched call
    video_ids = [e["youtube_video_id"] for e in episode_data]
    logger.info("Fetching current descriptions from YouTube...", video_ids=video_ids)
    descriptions = fetch_descriptions(youtube, video_ids)

    classified = []
    still_untagged = []

    for ep in episode_data:
        vid = ep["youtube_video_id"]
        title = ep["title"] or "(no title)"
        description = descriptions.get(vid, "")

        content_path, detection_method = detect_path(description)

        if content_path is None:
            still_untagged.append(ep)
            logger.warning(
                "Still no path tag in description — skipping",
                youtube_video_id=vid,
                title=title,
                hint="Add #PathA or #PathB to the YouTube description and re-run this script",
            )
            continue

        logger.info(
            "Tag detected — will classify",
            youtube_video_id=vid,
            title=title,
            content_path=content_path,
            dry_run=dry_run,
        )

        if not dry_run:
            with get_session() as session:
                episode = session.query(Episode).filter(
                    Episode.episode_id == ep["episode_id"]
                ).first()
                if episode:
                    episode.content_path = content_path
                    episode.path_detection_method = detection_method
                    episode.status = EpisodeStatus.CLASSIFIED
                    episode.updated_at = datetime.now(timezone.utc)

        classified.append({**ep, "content_path": content_path})

    # Summary
    print()
    print("=" * 60)
    print("RECLASSIFICATION SUMMARY")
    if dry_run:
        print("(DRY RUN — no changes written to database)")
    print("=" * 60)

    if classified:
        print(f"\n✓ Classified ({len(classified)}):")
        for ep in classified:
            print(f"  [{ep['content_path']}] {ep['title']}")
            print(f"           video_id: {ep['youtube_video_id']}")
    else:
        print("\n  No episodes classified.")

    if still_untagged:
        print(f"\n⚠ Still needs tagging ({len(still_untagged)}):")
        for ep in still_untagged:
            print(f"  {ep['title']}")
            print(f"    https://www.youtube.com/watch?v={ep['youtube_video_id']}")
            print(f"    → Add #PathA or #PathB to the YouTube description")
    else:
        print("\n  All episodes classified — pipeline will pick them up on next poll.")

    print()

    if classified and not dry_run:
        logger.info(
            "Reclassification complete — pipeline will process these episodes on next poll cycle",
            classified_count=len(classified),
            still_untagged_count=len(still_untagged),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Reclassify episodes stuck in needs_classification."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be classified without writing to the database.",
    )
    args = parser.parse_args()
    reclassify(dry_run=args.dry_run)
