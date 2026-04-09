"""
workers/buzzsprout_publisher.py
--------------------------------
Publishes episodes to Buzzsprout or simulates publishing in dark launch mode.

Dark launch (DARK_LAUNCH_MODE=true):
  - Packages the full intended Buzzsprout payload
  - Logs it for audit
  - Emails it to Carl for review
  - Creates an EpisodeOutput record with status=dark_launched
  - Does NOT touch Buzzsprout

Live mode (DARK_LAUNCH_MODE=false):
  - Uploads audio file to Buzzsprout
  - Creates the episode as a draft (private=true)
  - Stores the Buzzsprout episode ID in EpisodeOutput for idempotency

Idempotency: checks for an existing EpisodeOutput with type=podcast_episode
before creating a new Buzzsprout episode. Will never create duplicates.

Buzzsprout API:
  POST https://www.buzzsprout.com/api/{show_id}/episodes.json
  Authorization: Token token={api_token}
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Config
from database import get_session
from logger import get_logger
from models.episode_outputs import EpisodeOutput, OutputStatus, OutputType
from models.transcripts import Transcript

logger = get_logger(__name__)

BUZZSPROUT_API_BASE = "https://www.buzzsprout.com/api"


def publish_episode(episode, brand) -> EpisodeOutput:
    """
    Publish (or dark-launch) a podcast episode to Buzzsprout.
    Returns the EpisodeOutput record.
    """
    # Idempotency check — never create duplicate Buzzsprout episodes
    with get_session() as session:
        existing_output = session.query(EpisodeOutput).filter(
            EpisodeOutput.episode_id == episode.episode_id,
            EpisodeOutput.output_type == OutputType.PODCAST_EPISODE,
        ).first()

    if existing_output and existing_output.status in (
        OutputStatus.COMPLETED, OutputStatus.DARK_LAUNCHED
    ):
        logger.info(
            "Podcast episode already published/dark-launched — skipping",
            episode_id=str(episode.episode_id),
            output_status=existing_output.status,
            external_id=existing_output.external_id,
        )
        return existing_output

    if not brand.buzzsprout_show_id:
        raise RuntimeError(
            f"No buzzsprout_show_id configured for brand {brand.brand_name}. "
            "Update brand_config and re-run seed_brand_config.py."
        )

    # Load show notes from transcript
    with get_session() as session:
        transcript = session.query(Transcript).filter(
            Transcript.episode_id == episode.episode_id
        ).first()

    clean_transcript = transcript.clean_transcript if transcript else ""
    show_notes = _generate_show_notes(episode, brand, clean_transcript)

    # Build the Buzzsprout payload
    payload = {
        "title": episode.title or "Untitled Episode",
        "description": show_notes,
        "private": True,          # Always draft — Carl approves before going public
        "email_after_processing": False,
    }

    audio_path = episode.audio_file_path
    if not audio_path or not Path(audio_path).exists():
        raise RuntimeError(
            f"Audio file not found at {audio_path} for episode {episode.episode_id}. "
            "Re-run audio extraction."
        )

    if Config.DARK_LAUNCH_MODE:
        return _dark_launch(episode, brand, payload, audio_path, show_notes)
    else:
        return _live_publish(episode, brand, payload, audio_path)


def _dark_launch(episode, brand, payload: dict, audio_path: str, show_notes: str) -> EpisodeOutput:
    """
    Simulate publishing: log payload and email Carl. Do not touch Buzzsprout.
    """
    logger.info(
        "DARK LAUNCH — Buzzsprout publish simulated",
        episode_id=str(episode.episode_id),
        brand=brand.brand_name,
        buzzsprout_show_id=brand.buzzsprout_show_id,
        payload_title=payload["title"],
    )

    # Package the full review email
    email_body = _format_dark_launch_email(episode, brand, payload, audio_path, show_notes)

    try:
        from workers.notifier import send_dark_launch_email
        send_dark_launch_email(
            subject=f"[Dark Launch] New Episode Ready: {episode.title}",
            body=email_body,
            brand_name=brand.brand_name,
        )
    except Exception as exc:
        logger.warning("Dark launch email failed (non-fatal)", error=str(exc))

    # Record the dark launch output
    with get_session() as session:
        output = EpisodeOutput(
            episode_id=episode.episode_id,
            output_type=OutputType.PODCAST_EPISODE,
            status=OutputStatus.DARK_LAUNCHED,
            content=show_notes,
        )
        session.add(output)

    return output


@retry(
    wait=wait_exponential(multiplier=2, min=5, max=60),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _live_publish(episode, brand, payload: dict, audio_path: str) -> EpisodeOutput:
    """
    Upload audio and create the episode in Buzzsprout.
    Retries up to 4 times with exponential backoff.
    """
    api_url = f"{BUZZSPROUT_API_BASE}/{brand.buzzsprout_show_id}/episodes.json"
    headers = {
        "Authorization": f"Token token={Config.BUZZSPROUT_API_TOKEN}",
    }

    logger.info(
        "Publishing to Buzzsprout (live)",
        episode_id=str(episode.episode_id),
        brand=brand.brand_name,
        show_id=brand.buzzsprout_show_id,
    )

    with open(audio_path, "rb") as audio_file:
        files = {"audio_file": (Path(audio_path).name, audio_file, "audio/mpeg")}
        response = requests.post(
            api_url,
            headers=headers,
            data=payload,
            files=files,
            timeout=300,  # Large file uploads need a generous timeout
        )

    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Buzzsprout API error {response.status_code}: {response.text[:500]}"
        )

    result = response.json()
    buzzsprout_episode_id = str(result.get("id", ""))
    buzzsprout_url = result.get("audio_url", "")

    logger.info(
        "Buzzsprout episode created",
        episode_id=str(episode.episode_id),
        buzzsprout_episode_id=buzzsprout_episode_id,
        buzzsprout_url=buzzsprout_url,
    )

    with get_session() as session:
        output = EpisodeOutput(
            episode_id=episode.episode_id,
            output_type=OutputType.PODCAST_EPISODE,
            status=OutputStatus.COMPLETED,
            external_id=buzzsprout_episode_id,
            external_url=buzzsprout_url,
        )
        session.add(output)

    return output


def _generate_show_notes(episode, brand, clean_transcript: str) -> str:
    """
    Generate show notes from the clean transcript using GPT-4o.
    Targets brand.show_notes_word_count_min/max.
    Phase 3 will pull from the blog for Path A episodes.
    """
    if not clean_transcript:
        return f"Episode: {episode.title}"

    try:
        from openai import OpenAI
        client = OpenAI(api_key=Config.OPENAI_API_KEY)

        min_words = brand.show_notes_word_count_min or 300
        max_words = brand.show_notes_word_count_max or 600
        cta = brand.cta_url or ""

        system_prompt = f"""You are writing show notes for {brand.brand_name}.
Brand voice: {brand.tone_descriptor or "professional and clear"}
Target length: {min_words}–{max_words} words.

Write engaging show notes that:
1. Open with a 2-3 sentence hook summarising the episode value
2. Include 3-5 key takeaway bullet points
3. Close with a call to action{f": {cta}" if cta else ""}

Return only the show notes text. No title, no headers."""

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Episode title: {episode.title}\n\nTranscript:\n{clean_transcript[:8000]}"},
            ],
            temperature=0.5,
            max_tokens=1200,
        )
        return response.choices[0].message.content.strip()

    except Exception as exc:
        logger.warning("Show notes generation failed — using fallback", error=str(exc))
        return f"Episode: {episode.title}\n\n{clean_transcript[:500]}..."


def _format_dark_launch_email(episode, brand, payload: dict, audio_path: str, show_notes: str) -> str:
    """Format the dark launch review email body."""
    return f"""DARK LAUNCH REVIEW — Action Required

A new episode has been processed and is ready for your review before going live.

═══════════════════════════════════════
EPISODE DETAILS
═══════════════════════════════════════
Brand:          {brand.brand_name}
Show ID:        {brand.buzzsprout_show_id}
Title:          {episode.title}
YouTube URL:    {episode.youtube_url}
Audio File:     {audio_path}
Content Path:   {episode.content_path}
Processed At:   {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}

═══════════════════════════════════════
SHOW NOTES (to be published)
═══════════════════════════════════════
{show_notes}

═══════════════════════════════════════
NEXT STEPS
═══════════════════════════════════════
1. Review the show notes above
2. Log into Buzzsprout and manually create the episode using:
   - Show: {brand.brand_name} (ID: {brand.buzzsprout_show_id})
   - Title: {payload['title']}
   - Audio: Upload from {audio_path}
   - Status: Draft (private=true)
3. Review and publish when ready

To enable automatic publishing, set DARK_LAUNCH_MODE=false in Railway Variables.
"""
