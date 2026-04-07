"""
models/transcripts.py
---------------------
Stores raw and edited transcripts for each episode.
One-to-one with episodes. Both Postgres (here) and Google Drive
(.md file) are required storage targets per architecture spec §14.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class Transcript(Base):
    __tablename__ = "transcripts"

    transcript_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # --- FK to episode ---
    episode_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("episodes.episode_id"),
        unique=True,
        nullable=False,
        index=True,
    )
    episode: Mapped["Episode"] = relationship(  # type: ignore[name-defined]
        "Episode", back_populates="transcript"
    )

    # --- Transcript content ---
    raw_transcript: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # Whisper output, unedited
    clean_transcript: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # GPT-4o edited output

    # --- Whisper timestamp segments (JSON array) ---
    # Format: [{"start": 0.0, "end": 3.2, "text": "..."}, ...]
    timestamps_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Google Drive storage (populated after upload) ---
    # Naming convention: Transcripts/{brand}/{episode_id}-{slug}.md
    google_drive_file_id: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    google_drive_file_path: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Transcript episode_id={self.episode_id} has_clean={bool(self.clean_transcript)}>"
