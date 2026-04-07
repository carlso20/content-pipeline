"""
models/dead_letter_queue.py
----------------------------
Stores episodes and outputs that have permanently failed after
exhausting all retries. Full error context is preserved here for
manual inspection and requeue decisions.

When an item lands here, the orchestrator also:
  1. Creates a ClickUp task in the Approval Queue list
  2. Sends a notification email to Carl
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class DeadLetterQueueItem(Base):
    __tablename__ = "dead_letter_queue"

    dlq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # --- What failed ---
    # Either an episode_id or an output_id can be referenced (not both)
    episode_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    output_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    youtube_video_id: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    brand_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # --- Failure context ---
    failed_step: Mapped[str] = mapped_column(String(100), nullable=False)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    error_traceback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Downstream notification tracking ---
    clickup_task_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    notification_sent: Mapped[bool] = mapped_column(default=False, nullable=False)

    # --- Resolution ---
    resolved: Mapped[bool] = mapped_column(default=False, nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # --- Timestamps ---
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<DLQItem dlq_id={self.dlq_id} step={self.failed_step!r} "
            f"yt_id={self.youtube_video_id!r} resolved={self.resolved}>"
        )
