"""
workers/transcriber.py
-----------------------
Transcribes episode audio using OpenAI Whisper (whisper-1 model).

Produces:
  - Raw transcript text
  - Timestamps JSON array: [{"start": 0.0, "end": 3.2, "text": "..."}, ...]

Stores both in the transcripts table (creating the row if it doesn't exist).

Idempotency: if a raw_transcript already exists for this episode, transcription
is skipped and the existing record is returned.
"""

import json

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Config
from database import get_session
from logger import get_logger
from models.transcripts import Transcript

logger = get_logger(__name__)


def transcribe(episode, brand) -> Transcript:
    """
    Transcribe audio for an episode. Returns the Transcript record.
    Skips if raw_transcript already exists (idempotent).
    """
    # Idempotency check
    with get_session() as session:
        existing = session.query(Transcript).filter(
            Transcript.episode_id == episode.episode_id
        ).first()
        if existing and existing.raw_transcript:
            logger.info(
                "Transcript already exists — skipping Whisper call",
                episode_id=str(episode.episode_id),
            )
            return existing

    audio_path = episode.audio_file_path
    if not audio_path:
        raise RuntimeError(
            f"No audio_file_path set for episode {episode.episode_id}. "
            "Run audio extraction first."
        )

    logger.info(
        "Starting Whisper transcription",
        episode_id=str(episode.episode_id),
        brand=brand.brand_name,
        audio_path=audio_path,
    )

    raw_text, timestamps_json = _call_whisper(audio_path)

    logger.info(
        "Whisper transcription complete",
        episode_id=str(episode.episode_id),
        word_count=len(raw_text.split()),
        segments=len(json.loads(timestamps_json)),
    )

    # Persist to database
    with get_session() as session:
        transcript = session.query(Transcript).filter(
            Transcript.episode_id == episode.episode_id
        ).first()

        if transcript:
            transcript.raw_transcript = raw_text
            transcript.timestamps_json = timestamps_json
        else:
            transcript = Transcript(
                episode_id=episode.episode_id,
                raw_transcript=raw_text,
                timestamps_json=timestamps_json,
            )
            session.add(transcript)

    return transcript


@retry(
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _call_whisper(audio_path: str) -> tuple[str, str]:
    """
    Call the Whisper API with verbose_json to get text + segment timestamps.
    Returns (raw_text, timestamps_json_string).
    Retries up to 3 times with exponential backoff.
    """
    client = OpenAI(api_key=Config.OPENAI_API_KEY)

    with open(audio_path, "rb") as audio_file:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )

    raw_text = response.text

    # Extract segment timestamps into a clean JSON array
    segments = []
    if hasattr(response, "segments") and response.segments:
        for seg in response.segments:
            segments.append({
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
            })

    timestamps_json = json.dumps(segments, ensure_ascii=False)
    return raw_text, timestamps_json
