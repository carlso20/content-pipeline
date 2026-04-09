"""
workers/audio_extractor.py
---------------------------
Downloads audio from a YouTube video and extracts a podcast-ready MP3
using yt-dlp and ffmpeg.

Idempotency: if the audio file already exists at the stored path AND the
checksum matches the database record, extraction is skipped and the existing
file is returned. This handles Railway restarts gracefully — files in /tmp
are ephemeral, so a missing file triggers a re-download rather than a crash.

Output: /tmp/{episode_id}.mp3
"""

import hashlib
import os
import tempfile
from pathlib import Path

import yt_dlp
from tenacity import retry, stop_after_attempt, wait_exponential

from logger import get_logger

logger = get_logger(__name__)

AUDIO_DIR = Path(tempfile.gettempdir()) / "content_pipeline_audio"
AUDIO_DIR.mkdir(exist_ok=True)


def extract_audio(episode) -> tuple[str, str, int]:
    """
    Download and extract audio for an episode.

    Returns:
        (audio_file_path, sha256_checksum, duration_seconds)

    Raises:
        RuntimeError if download or extraction fails.
    """
    audio_path = AUDIO_DIR / f"{episode.episode_id}.mp3"

    # Idempotency check: skip if file exists and checksum matches
    if audio_path.exists() and episode.audio_checksum:
        current_checksum = _sha256(audio_path)
        if current_checksum == episode.audio_checksum:
            logger.info(
                "Audio file already exists and checksum matches — skipping extraction",
                episode_id=str(episode.episode_id),
                path=str(audio_path),
            )
            duration = _get_duration(audio_path)
            return str(audio_path), current_checksum, duration

    # File missing or checksum mismatch — download fresh
    logger.info(
        "Downloading audio",
        episode_id=str(episode.episode_id),
        youtube_url=episode.youtube_url,
    )

    _download_audio(episode.youtube_url, audio_path)

    checksum = _sha256(audio_path)
    duration = _get_duration(audio_path)

    logger.info(
        "Audio extraction complete",
        episode_id=str(episode.episode_id),
        path=str(audio_path),
        checksum=checksum,
        duration_seconds=duration,
        size_mb=round(audio_path.stat().st_size / 1_000_000, 2),
    )

    return str(audio_path), checksum, duration


@retry(
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _download_audio(youtube_url: str, output_path: Path) -> None:
    """
    Use yt-dlp to download best available audio and convert to MP3 via ffmpeg.
    Retries up to 3 times with exponential backoff.
    """
    # Output template without extension — yt-dlp adds .mp3 via postprocessor
    output_template = str(output_path.with_suffix(""))

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
        # Normalize audio levels during extraction
        "postprocessor_args": [
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11"
        ],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])

    if not output_path.exists():
        raise RuntimeError(
            f"yt-dlp completed but output file not found at {output_path}"
        )


def _sha256(path: Path) -> str:
    """Compute SHA-256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _get_duration(path: Path) -> int:
    """
    Get audio duration in seconds using ffprobe.
    Returns 0 if ffprobe is unavailable or fails.
    """
    import subprocess
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return int(float(result.stdout.strip()))
    except Exception as exc:
        logger.warning("Could not determine audio duration", error=str(exc))
        return 0
