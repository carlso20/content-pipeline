"""
scripts/remove_shorts.py
-------------------------
One-shot cleanup script to remove episode records that slipped past the
duration filter — specifically Shorts or other videos where duration_seconds
is NULL or below the 7-minute minimum.

These records can't be processed by the pipeline and would block classification
runs indefinitely. This script deletes them from the episodes table so they
won't appear in future reclassify runs.

Usage:
    # Dry-run — show what would be deleted without touching the DB:
    python scripts/remove_shorts.py --dry-run

    # Delete for real:
    python scripts/remove_shorts.py
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from database import init_db, get_session
from logger import configure_logging, get_logger
from models.episodes import Episode, EpisodeStatus

configure_logging()
logger = get_logger(__name__)

MIN_EPISODE_DURATION_SECONDS = 7 * 60  # Must match youtube_poller.py


def remove_shorts(dry_run: bool = False) -> None:
    init_db()

    with get_session() as session:
        # Find episodes with NULL or sub-threshold duration that are still
        # awaiting classification (haven't been manually advanced).
        candidates = (
            session.query(Episode)
            .filter(
                Episode.status == EpisodeStatus.NEEDS_CLASSIFICATION,
                (Episode.duration_seconds == None)  # noqa: E711
                | (Episode.duration_seconds < MIN_EPISODE_DURATION_SECONDS),
            )
            .all()
        )

        rows = [
            {
                "episode_id": e.episode_id,
                "youtube_video_id": e.youtube_video_id,
                "title": e.title,
                "duration_seconds": e.duration_seconds,
            }
            for e in candidates
        ]

    print()
    print("=" * 60)
    print("SHORTS / DURATION-FAILED EPISODE CLEANUP")
    if dry_run:
        print("(DRY RUN — no changes written to database)")
    print("=" * 60)

    if not rows:
        print("\n  No episodes found with missing or sub-threshold duration.")
        print()
        return

    print(f"\nFound {len(rows)} episode(s) to remove:\n")
    for r in rows:
        dur = r["duration_seconds"]
        dur_str = f"{dur}s ({dur // 60}m)" if dur else "NULL"
        print(f"  [{dur_str}] {r['title']}")
        print(f"           video_id: {r['youtube_video_id']}")
    print()

    if dry_run:
        print("  Run without --dry-run to delete these records.")
        print()
        return

    # Delete
    with get_session() as session:
        for r in rows:
            episode = session.query(Episode).filter(
                Episode.episode_id == r["episode_id"]
            ).first()
            if episode:
                session.delete(episode)
                logger.info(
                    "Deleted short/unconfirmed episode",
                    youtube_video_id=r["youtube_video_id"],
                    title=r["title"],
                    duration_seconds=r["duration_seconds"],
                )

    print(f"  Deleted {len(rows)} record(s). They will not be re-detected")
    print("  (idempotency check uses video ID; these IDs are now free to be")
    print("   re-evaluated on the next poll if they've since become full episodes).")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Remove episode records with missing or sub-threshold duration."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without writing to the database.",
    )
    args = parser.parse_args()
    remove_shorts(dry_run=args.dry_run)
