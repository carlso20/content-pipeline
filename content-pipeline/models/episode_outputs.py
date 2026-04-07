"""
models/episode_outputs.py
--------------------------
Tracks all derivative outputs generated from each episode.
One episode produces multiple output rows (one per output type).

External system IDs (Buzzsprout episode ID, WordPress post ID, etc.)
are stored here to enforce idempotency — workers check for existing
records before creating new ones in downstream systems.
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class OutputType(str, PyEnum):
    PODCAST_EPISODE = "podcast_episode"       # Buzzsprout publish
    SHOW_NOTES = "show_notes"
    YOUTUBE_DESCRIPTION = "youtube_description"
    CHAPTER_MARKERS = "chapter_markers"
    SOCIAL_POSTS = "social_posts"             # ClickUp task with attached posts
    EMAIL_SECTION = "email_section"           # Newsletter section draft
    QUOTE_CARD_INPUTS = "quote_card_inputs"   # Inputs for graphic design
    BLOG_DRAFT = "blog_draft"                 # Path B only; WordPress draft
    CLEAN_TRANSCRIPT = "clean_transcript"     # Google Drive storage confirmation


class OutputStatus(str, PyEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DARK_LAUNCHED = "dark_launched"   # Payload logged + emailed; not published live
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"               # Not applicable for this content path


class EpisodeOutput(Base):
    __tablename__ = "episode_outputs"

    output_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # --- FK to episode ---
    episode_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("episodes.episode_id"),
        nullable=False,
        index=True,
    )
    episode: Mapped["Episode"] = relationship(  # type: ignore[name-defined]
        "Episode", back_populates="outputs"
    )

    # --- Output classification ---
    output_type: Mapped[OutputType] = mapped_column(
        Enum(OutputType, name="output_type_enum"), nullable=False, index=True
    )
    status: Mapped[OutputStatus] = mapped_column(
        Enum(OutputStatus, name="output_status_enum"),
        default=OutputStatus.PENDING,
        nullable=False,
    )

    # --- Content ---
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- External system IDs (for idempotency) ---
    # e.g., Buzzsprout episode ID, WordPress post ID, ClickUp task ID
    external_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    external_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Error tracking ---
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(default=0, nullable=False)

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
        return (
            f"<EpisodeOutput episode_id={self.episode_id} "
            f"type={self.output_type} status={self.status}>"
        )
