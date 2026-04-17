"""
scripts/seed_brand_config.py
-----------------------------
Seeds the brand_config table with the three Simplicity Marketing LLC shows.

Run AFTER the database is initialized:
    python scripts/seed_brand_config.py

Channel ID resolution (priority order):
  1. YOUTUBE_CHANNEL_ID_* environment variable (explicit override)
  2. Auto-resolve from YouTube channel handle via Data API v3
     (requires YOUTUBE_API_KEY to be set)
  3. UC_TODO_* placeholder (warns loudly — channel will be skipped at poll time)

Buzzsprout show IDs must be filled in manually once you have them from
your Buzzsprout account dashboard, or set via BUZZSPROUT_SHOW_ID_* env vars.

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
# Brand definitions — channel IDs resolved at runtime
# ============================================================
BRAND_TEMPLATES = [
    {
        "brand_name": "Remodeling Marketing Team",
        "youtube_channel_handle": "@remodelingmarketingteam5627",
        "youtube_channel_id_env": "YOUTUBE_CHANNEL_ID_REMODELING_MARKETING",
        "buzzsprout_show_id_env": "BUZZSPROUT_SHOW_ID_REMODELING",
        "buzzsprout_show_id_default": "2125225",
        "rag_domains": "remodeling, home improvement, contractor marketing, home services",
        "rag_client_scope": "remodeling_contractors",
        "cta_url_env": "CTA_URL_REMODELING",
        "blog_word_count_min": 1200,
        "blog_word_count_max": 2500,
        "show_notes_word_count_min": 300,
        "show_notes_word_count_max": 600,
        "tone_descriptor": "professional, educational, contractor-focused, practical and actionable",
        "wordpress_url_env": "WORDPRESS_URL_REMODELING",
        "wordpress_env_key_prefix": "WORDPRESS_REMODELING",
        "is_active": True,
    },
    {
        "brand_name": "Agent Branding and Marketing",
        "youtube_channel_handle": "@agentbrandingmarketing5528",
        "youtube_channel_id_env": "YOUTUBE_CHANNEL_ID_AGENT_BRANDING",
        "buzzsprout_show_id_env": "BUZZSPROUT_SHOW_ID_AGENT_BRANDING",
        "buzzsprout_show_id_default": "2168365",
        "rag_domains": "real estate, agent branding, marketing, ABM, lead generation",
        "rag_client_scope": "real_estate_agents",
        "cta_url_env": "CTA_URL_AGENT_BRANDING",
        "blog_word_count_min": 1200,
        "blog_word_count_max": 2500,
        "show_notes_word_count_min": 300,
        "show_notes_word_count_max": 600,
        "tone_descriptor": "professional, strategic, agent-focused, brand-building mindset",
        "wordpress_url_env": "WORDPRESS_URL_AGENT_BRANDING",
        "wordpress_env_key_prefix": "WORDPRESS_AGENT_BRANDING",
        "is_active": True,
    },
    {
        "brand_name": "Carl Willis",
        "youtube_channel_handle": "@CarlWillis20",
        "youtube_channel_id_env": "YOUTUBE_CHANNEL_ID_CARL_WILLIS",
        "buzzsprout_show_id_env": "BUZZSPROUT_SHOW_ID_CARL_WILLIS",
        "buzzsprout_show_id_default": "2511435",
        "rag_domains": "entrepreneurship, business strategy, marketing, leadership, called to build",
        "rag_client_scope": "general_business",
        "cta_url_env": "CTA_URL_CARL_WILLIS",
        "blog_word_count_min": 1000,
        "blog_word_count_max": 2000,
        "show_notes_word_count_min": 250,
        "show_notes_word_count_max": 500,
        "tone_descriptor": "authentic, direct, entrepreneur-focused, personal and story-driven",
        "wordpress_url_env": "WORDPRESS_URL_CARL_WILLIS",
        "wordpress_env_key_prefix": "WORDPRESS_CARL_WILLIS",
        "is_active": True,
    },
]


def resolve_channel_id(youtube_client, handle: str, brand_name: str) -> str | None:
    """
    Resolve a YouTube channel handle to its UCxxxxxx channel ID.
    Uses the YouTube Data API v3 channels.list endpoint with forHandle parameter.
    Returns None if resolution fails.
    """
    clean_handle = handle.lstrip("@")
    try:
        response = youtube_client.channels().list(
            part="id,snippet",
            forHandle=clean_handle,
        ).execute()
        items = response.get("items", [])
        if items:
            channel_id = items[0]["id"]
            logger.info(
                "Resolved YouTube channel ID",
                brand=brand_name,
                handle=handle,
                channel_id=channel_id,
            )
            return channel_id
        else:
            logger.warning(
                "YouTube channel handle not found",
                brand=brand_name,
                handle=handle,
            )
            return None
    except Exception as exc:
        logger.error(
            "Failed to resolve YouTube channel handle",
            brand=brand_name,
            handle=handle,
            error=str(exc),
        )
        return None


def build_brand_data(template: dict, youtube_client) -> dict:
    """
    Build a brand_config row dict from a template.

    Channel ID resolution order:
      1. Env var (explicit override)
      2. Auto-resolve from YouTube API
      3. UC_TODO_* placeholder (warns loudly)
    """
    brand_name = template["brand_name"]
    handle = template["youtube_channel_handle"]
    env_key = template["youtube_channel_id_env"]

    # 1. Check explicit env var
    channel_id = os.environ.get(env_key)

    # 2. Auto-resolve via YouTube API if not set
    if not channel_id and youtube_client:
        channel_id = resolve_channel_id(youtube_client, handle, brand_name)

    # 3. Fall back to placeholder (will be skipped at poll time)
    if not channel_id:
        placeholder = f"UC_TODO_{handle.lstrip('@')}"
        logger.warning(
            "Could not resolve channel ID — using placeholder. "
            "Set %s in Railway Variables or ensure YOUTUBE_API_KEY is valid.",
            env_key,
            brand=brand_name,
            channel_id=placeholder,
        )
        channel_id = placeholder

    return {
        "brand_name": brand_name,
        "youtube_channel_handle": handle,
        "youtube_channel_id": channel_id,
        "buzzsprout_show_id": (
            os.environ.get(template["buzzsprout_show_id_env"])
            or template["buzzsprout_show_id_default"]
        ),
        "rag_domains": template["rag_domains"],
        "rag_client_scope": template["rag_client_scope"],
        "cta_url": os.environ.get(template["cta_url_env"]),
        "blog_word_count_min": template["blog_word_count_min"],
        "blog_word_count_max": template["blog_word_count_max"],
        "show_notes_word_count_min": template["show_notes_word_count_min"],
        "show_notes_word_count_max": template["show_notes_word_count_max"],
        "tone_descriptor": template["tone_descriptor"],
        "wordpress_site_url": os.environ.get(template["wordpress_url_env"]),
        "wordpress_env_key_prefix": template["wordpress_env_key_prefix"],
        "is_active": template["is_active"],
    }


def seed() -> None:
    init_db()

    # Attempt to build a YouTube API client for handle resolution
    youtube_client = None
    if Config.YOUTUBE_API_KEY:
        try:
            from googleapiclient.discovery import build
            youtube_client = build("youtube", "v3", developerKey=Config.YOUTUBE_API_KEY)
            logger.info("YouTube API client ready for channel ID resolution")
        except Exception as exc:
            logger.warning(
                "Could not initialize YouTube API client — channel IDs will use env vars or placeholders",
                error=str(exc),
            )
    else:
        logger.warning(
            "YOUTUBE_API_KEY not set — channel IDs will use env vars or UC_TODO_* placeholders"
        )

    brands_data = [build_brand_data(t, youtube_client) for t in BRAND_TEMPLATES]

    with get_session() as session:
        for brand_data in brands_data:
            existing = (
                session.query(BrandConfig)
                .filter(BrandConfig.brand_name == brand_data["brand_name"])
                .first()
            )

            if existing:
                # Always update — ensure channel IDs and settings stay current
                for key, value in brand_data.items():
                    if value is not None:
                        setattr(existing, key, value)
                logger.info(
                    "Updated brand config",
                    brand=brand_data["brand_name"],
                    channel_id=brand_data["youtube_channel_id"],
                )
            else:
                brand = BrandConfig(**{k: v for k, v in brand_data.items() if v is not None})
                session.add(brand)
                logger.info(
                    "Created brand config",
                    brand=brand_data["brand_name"],
                    channel_id=brand_data["youtube_channel_id"],
                )

    # Summary
    resolved_count = sum(
        1 for b in brands_data if not b["youtube_channel_id"].startswith("UC_TODO_")
    )
    unresolved = [
        b["brand_name"] for b in brands_data if b["youtube_channel_id"].startswith("UC_TODO_")
    ]

    logger.info(
        "Brand config seeding complete",
        brands_seeded=len(brands_data),
        channel_ids_resolved=resolved_count,
    )

    if unresolved:
        logger.warning(
            "Some brands have unresolved channel IDs — they will be skipped during polling. "
            "Set the following Railway Variables to fix this:",
            unresolved_brands=unresolved,
            fix="Set YOUTUBE_CHANNEL_ID_* vars in Railway → Variables, then re-run this script "
                "OR ensure YOUTUBE_API_KEY is set and re-deploy",
        )


if __name__ == "__main__":
    seed()
