"""
scripts/seed_brand_config.py
-----------------------------
Seeds the brand_config table with the three Simplicity Marketing LLC shows.

Run AFTER the database is initialized:
    python scripts/seed_brand_config.py

YouTube channel IDs (UCxxxxxx format) must be resolved before seeding.
Run scripts/resolve_channel_ids.py first OR set the
YOUTUBE_CHANNEL_ID_* environment variables in your .env.

Buzzsprout show IDs must be filled in manually once you have them from
your Buzzsprout account dashboard.

This script is idempotent: running it multiple times will update existing
records rather than creating duplicates.
"""

import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from database import init_db, get_session
from logger import configure_logging, get_logger
from models.brand_config import BrandConfig

configure_logging()
logger = get_logger(__name__)

# ============================================================
# Brand definitions
# Fill in TODOs before deploying to production.
# youtube_channel_id: resolve via scripts/resolve_channel_ids.py
# buzzsprout_show_id: find in Buzzsprout dashboard → Settings → API
# ============================================================
BRANDS = [
    {
        "brand_name": "Remodeling Marketing Team",
        "youtube_channel_handle": "@remodelingmarketingteam5627",
        "youtube_channel_id": os.environ.get(
            "YOUTUBE_CHANNEL_ID_REMODELING_MARKETING",
            "UC_TODO_remodelingmarketingteam5627",  # Replace after resolving
        ),
        "buzzsprout_show_id": os.environ.get("BUZZSPROUT_SHOW_ID_REMODELING", None),  # TODO
        "rag_domains": "remodeling, home improvement, contractor marketing, home services",
        "rag_client_scope": "remodeling_contractors",
        "cta_url": os.environ.get("CTA_URL_REMODELING", None),  # TODO: Add landing page URL
        "blog_word_count_min": 1200,
        "blog_word_count_max": 2500,
        "show_notes_word_count_min": 300,
        "show_notes_word_count_max": 600,
        "tone_descriptor": "professional, educational, contractor-focused, practical and actionable",
        "wordpress_site_url": os.environ.get("WORDPRESS_URL_REMODELING", None),  # TODO
        "wordpress_env_key_prefix": "WORDPRESS_REMODELING",
        "is_active": True,
    },
    {
        "brand_name": "Agent Branding and Marketing",
        "youtube_channel_handle": "@agentbrandingmarketing5528",
        "youtube_channel_id": os.environ.get(
            "YOUTUBE_CHANNEL_ID_AGENT_BRANDING",
            "UC_TODO_agentbrandingmarketing5528",  # Replace after resolving
        ),
        "buzzsprout_show_id": os.environ.get("BUZZSPROUT_SHOW_ID_AGENT_BRANDING", None),  # TODO
        "rag_domains": "real estate, agent branding, marketing, ABM, lead generation",
        "rag_client_scope": "real_estate_agents",
        "cta_url": os.environ.get("CTA_URL_AGENT_BRANDING", None),  # TODO
        "blog_word_count_min": 1200,
        "blog_word_count_max": 2500,
        "show_notes_word_count_min": 300,
        "show_notes_word_count_max": 600,
        "tone_descriptor": "professional, strategic, agent-focused, brand-building mindset",
        "wordpress_site_url": os.environ.get("WORDPRESS_URL_AGENT_BRANDING", None),  # TODO
        "wordpress_env_key_prefix": "WORDPRESS_AGENT_BRANDING",
        "is_active": True,
    },
    {
        "brand_name": "Carl Willis",
        "youtube_channel_handle": "@CarlWillis20",
        "youtube_channel_id": os.environ.get(
            "YOUTUBE_CHANNEL_ID_CARL_WILLIS",
            "UC_TODO_CarlWillis20",  # Replace after resolving
        ),
        "buzzsprout_show_id": os.environ.get("BUZZSPROUT_SHOW_ID_CARL_WILLIS", None),  # TODO
        "rag_domains": "entrepreneurship, business strategy, marketing, leadership, called to build",
        "rag_client_scope": "general_business",
        "cta_url": os.environ.get("CTA_URL_CARL_WILLIS", None),  # TODO
        "blog_word_count_min": 1000,
        "blog_word_count_max": 2000,
        "show_notes_word_count_min": 250,
        "show_notes_word_count_max": 500,
        "tone_descriptor": "authentic, direct, entrepreneur-focused, personal and story-driven",
        "wordpress_site_url": os.environ.get("WORDPRESS_URL_CARL_WILLIS", None),  # TODO
        "wordpress_env_key_prefix": "WORDPRESS_CARL_WILLIS",
        "is_active": True,
    },
]


def seed() -> None:
    init_db()

    with get_session() as session:
        for brand_data in BRANDS:
            existing = (
                session.query(BrandConfig)
                .filter(BrandConfig.brand_name == brand_data["brand_name"])
                .first()
            )

            if existing:
                # Update existing record (idempotent)
                for key, value in brand_data.items():
                    if value is not None:
                        setattr(existing, key, value)
                logger.info("Updated brand config", brand=brand_data["brand_name"])
            else:
                # Create new record
                brand = BrandConfig(**{k: v for k, v in brand_data.items() if v is not None})
                session.add(brand)
                logger.info("Created brand config", brand=brand_data["brand_name"])

    logger.info(
        "Brand config seeding complete",
        brands_seeded=len(BRANDS),
        reminder=(
            "IMPORTANT: Replace UC_TODO_* channel IDs with real UCxxxxxx values. "
            "Run scripts/resolve_channel_ids.py to resolve them automatically."
        ),
    )


if __name__ == "__main__":
    seed()
