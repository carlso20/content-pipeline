"""
scripts/classify_existing_episodes.py
--------------------------------------
One-time script to bulk-classify existing episodes as Path A.

Run this when you know all currently-detected episodes should be Path A:
    python scripts/classify_existing_episodes.py

By default classifies ALL needs_classification episodes as Path A.
Pass --brand "Brand Name" to restrict to a specific brand.
Pass --dry-run to preview without writing.

After running this, the pipeline will pick them up on the next poll cycle
and begin audio extraction → transcription → editing → dark-launch publishing.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db, get_session
from logger import configure_logging, get_logger
from models.brand_config import BrandConfig
from models.episodes import ContentPath, Episode, EpisodeStatus

configure_logging()
logger = get_logger(__name__)


def classify(brand_filter: str | None = None, dry_run: bool = False) -> None:
    init_db()

    with get_session() as session:
        query = session.query(Episode).filter(
            Episode.status == EpisodeStatus.NEEDS_CLASSIFICATION
        )

        if brand_filter:
            brand = (
                session.query(BrandConfig)
                .filter(BrandConfig.brand_name.ilike(f"%{brand_filter}%"))
                .first()
            )
            if not brand:
                print(f"\nERROR: No brand found matching '{brand_filter}'\n")
                sys.exit(1)
            query = query.filter(Episode.brand_config_id == brand.id)

        episodes = query.all()

        if not episodes:
            print("\nNo episodes in needs_classification status found.\n")
            return

        print(f"\n{'DRY RUN — ' if dry_run else ''}Classifying {len(episodes)} episodes as Path A:\n")
        for ep in episodes:
            brand = session.query(BrandConfig).filter(
                BrandConfig.id == ep.brand_config_id
            ).first()
            brand_name = brand.brand_name if brand else "Unknown"
            print(f"  [{brand_name}] {ep.title[:80]}")
            if not dry_run:
                ep.content_path = ContentPath.PATH_A
                ep.path_detection_method = "manual_bulk_classification"
                ep.status = EpisodeStatus.CLASSIFIED

        if dry_run:
            print(f"\nDry run complete — no changes written.\n")
        else:
            print(f"\n✓ {len(episodes)} episodes classified as Path A and queued for processing.\n")
            logger.info(
                "Bulk Path A classification complete",
                episodes_classified=len(episodes),
                brand_filter=brand_filter or "all",
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk-classify episodes as Path A")
    parser.add_argument("--brand", help="Only classify episodes for this brand (partial match)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing changes")
    args = parser.parse_args()

    classify(brand_filter=args.brand, dry_run=args.dry_run)
