"""
scripts/resolve_channel_ids.py
-------------------------------
Resolves YouTube channel handles (@username) to canonical UCxxxxxx channel IDs
using the YouTube Data API v3 channels.list endpoint with forHandle parameter.

Run BEFORE seeding brand_config:
    python scripts/resolve_channel_ids.py

Output: Prints the resolved channel IDs and instructions for updating .env
and re-running the seed script.

Requires: YOUTUBE_API_KEY must be set in environment or .env
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from googleapiclient.discovery import build
from config import Config
from logger import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

# Handles to resolve — update if brand handles change
HANDLES_TO_RESOLVE = [
    ("Remodeling Marketing Team", "@remodelingmarketingteam5627", "YOUTUBE_CHANNEL_ID_REMODELING_MARKETING"),
    ("Agent Branding and Marketing", "@agentbrandingmarketing5528", "YOUTUBE_CHANNEL_ID_AGENT_BRANDING"),
    ("Carl Willis", "@CarlWillis20", "YOUTUBE_CHANNEL_ID_CARL_WILLIS"),
]


def resolve_handle(youtube_client, handle: str) -> str | None:
    """
    Calls YouTube channels.list with forHandle to get the UCxxxxxx channel ID.
    Returns None if the handle cannot be resolved.
    """
    # Strip the @ prefix if present
    clean_handle = handle.lstrip("@")
    try:
        response = youtube_client.channels().list(
            part="id,snippet",
            forHandle=clean_handle,
        ).execute()

        items = response.get("items", [])
        if items:
            return items[0]["id"]
        else:
            logger.warning("Handle not found via API", handle=handle)
            return None
    except Exception as exc:
        logger.error("Failed to resolve handle", handle=handle, error=str(exc))
        return None


def main() -> None:
    if not Config.YOUTUBE_API_KEY:
        print("\nERROR: YOUTUBE_API_KEY is not set. Set it in .env and retry.\n")
        sys.exit(1)

    youtube = build("youtube", "v3", developerKey=Config.YOUTUBE_API_KEY)

    print("\n" + "=" * 60)
    print("Resolving YouTube channel handles → IDs")
    print("=" * 60)

    resolved = {}
    for brand_name, handle, env_key in HANDLES_TO_RESOLVE:
        channel_id = resolve_handle(youtube, handle)
        status = "✓" if channel_id else "✗ NOT FOUND"
        print(f"\n  Brand:      {brand_name}")
        print(f"  Handle:     {handle}")
        print(f"  Channel ID: {channel_id or 'NOT RESOLVED'} {status}")
        print(f"  Env key:    {env_key}={channel_id or 'TODO'}")
        if channel_id:
            resolved[env_key] = channel_id

    print("\n" + "=" * 60)
    print("Next steps:")
    print("=" * 60)
    print("\n1. Add these lines to your Railway Variables or .env:\n")
    for env_key, channel_id in resolved.items():
        print(f"   {env_key}={channel_id}")

    if len(resolved) < len(HANDLES_TO_RESOLVE):
        unresolved = [h for _, h, k in HANDLES_TO_RESOLVE if k not in resolved]
        print(f"\n⚠  Could not resolve: {unresolved}")
        print("   Check that the channel handles are correct and the API key has")
        print("   YouTube Data API v3 enabled in Google Cloud Console.")

    print("\n2. Run the seed script to update brand_config:")
    print("   python scripts/seed_brand_config.py\n")


if __name__ == "__main__":
    main()
