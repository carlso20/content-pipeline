"""
workers/transcript_editor.py
-----------------------------
GPT-4o editing pass on the raw Whisper transcript.

Editing tasks:
  - Spelling and grammar correction
  - Filler word removal (um, uh, like, you know, basically, literally)
  - Run-on sentence cleanup and paragraph formatting
  - Brand voice alignment using brand.tone_descriptor and brand.rag_domains

Path A logic:
  - The blog post is the authoritative source for phrasing and factual framing.
  - Transcript is reconciled against the blog — keep the blog's language where
    it differs from the raw transcript on substantive points.
  - (Phase 3: actual blog retrieval. Phase 2: internal consistency only.)

Path B logic:
  - The clean transcript IS the canonical content foundation.
  - Edit for clarity, readability, and brand voice. No external reconciliation.

Idempotency: if clean_transcript already exists, the editing step is skipped.
"""

from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Config
from database import get_session
from logger import get_logger
from models.episodes import ContentPath
from models.transcripts import Transcript

logger = get_logger(__name__)

FILLER_WORDS = [
    "um", "uh", "umm", "uhh", "like", "you know", "basically", "literally",
    "actually", "honestly", "right", "okay so", "so yeah", "I mean",
]


def edit_transcript(episode, brand) -> str:
    """
    Run GPT-4o editing pass on the raw transcript.
    Returns the clean transcript text.
    Skips if clean_transcript already exists (idempotent).
    """
    with get_session() as session:
        transcript = session.query(Transcript).filter(
            Transcript.episode_id == episode.episode_id
        ).first()

    if not transcript:
        raise RuntimeError(
            f"No transcript record found for episode {episode.episode_id}. "
            "Run transcription first."
        )

    # Idempotency check
    if transcript.clean_transcript:
        logger.info(
            "Clean transcript already exists — skipping GPT-4o pass",
            episode_id=str(episode.episode_id),
        )
        return transcript.clean_transcript

    if not transcript.raw_transcript:
        raise RuntimeError(
            f"raw_transcript is empty for episode {episode.episode_id}."
        )

    logger.info(
        "Starting GPT-4o transcript editing",
        episode_id=str(episode.episode_id),
        brand=brand.brand_name,
        content_path=str(episode.content_path),
        raw_word_count=len(transcript.raw_transcript.split()),
    )

    clean_text = _call_gpt4o(
        raw_transcript=transcript.raw_transcript,
        episode_title=episode.title or "",
        content_path=episode.content_path,
        brand=brand,
    )

    logger.info(
        "GPT-4o editing complete",
        episode_id=str(episode.episode_id),
        clean_word_count=len(clean_text.split()),
    )

    # Persist clean transcript
    with get_session() as session:
        t = session.query(Transcript).filter(
            Transcript.episode_id == episode.episode_id
        ).first()
        if t:
            t.clean_transcript = clean_text

    return clean_text


@retry(
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(3),
    reraise=True,
)
def _call_gpt4o(
    raw_transcript: str,
    episode_title: str,
    content_path,
    brand,
) -> str:
    """
    Call GPT-4o to produce a clean, publication-ready transcript.
    """
    client = OpenAI(api_key=Config.OPENAI_API_KEY)

    filler_list = ", ".join(f'"{w}"' for w in FILLER_WORDS)

    if content_path == ContentPath.PATH_A:
        path_instruction = (
            "This episode has an existing blog post (Path A). "
            "Edit for clarity and brand voice. If the transcript contradicts "
            "factual claims that would be in a blog post, flag them with "
            "[VERIFY] inline but do not alter the meaning. "
            "Treat the spoken content as the primary source — you are cleaning "
            "it, not rewriting it."
        )
    else:
        path_instruction = (
            "This episode has no existing blog post (Path B). "
            "The clean transcript you produce will be the canonical content "
            "source for all derivative outputs. Edit thoroughly for clarity, "
            "readability, and internal consistency. Ensure all claims are "
            "self-consistent within the transcript."
        )

    system_prompt = f"""You are an expert podcast transcript editor for {brand.brand_name}.

Brand voice: {brand.tone_descriptor or "professional and clear"}
Domain focus: {brand.rag_domains or "general business and marketing"}

Your editing tasks:
1. Remove filler words: {filler_list}
2. Fix spelling, grammar, and punctuation
3. Break run-on sentences into clear, readable sentences
4. Organize content into logical paragraphs (blank line between paragraphs)
5. Preserve the speaker's authentic voice — do not paraphrase or rewrite ideas
6. Do not add content that was not spoken
7. Do not include timestamps, speaker labels, or formatting markup

{path_instruction}

Return ONLY the clean transcript text. No preamble, no explanation, no headers."""

    user_prompt = f"""Episode title: {episode_title}

Raw transcript:
{raw_transcript}"""

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,   # Low temperature for consistent editing
        max_tokens=16000,
    )

    return response.choices[0].message.content.strip()
