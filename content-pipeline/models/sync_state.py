"""
models/sync_state.py
---------------------
Tracks the last-seen YouTube video ID per brand channel so the
polling worker can compare new results against known uploads.

One row per brand_config. Updated after each successful polling cycle.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class SyncState(Base):
    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- Brand FK (one sync state row per brand) ---
    brand_config_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("brand_config.id"),
        unique=True,
        nullable=False,
        index=True,
    )
    brand_config: Mapped["BrandConfig"] = relationship(  # type: ignore[name-defined]
        "BrandConfig", lazy="select"
    )

    # --- Last known state ---
    last_youtube_video_id: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True
    )
    last_polled_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_successful_poll_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

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
            f"<SyncState brand_config_id={self.brand_config_id} "
            f"last_yt_id={self.last_youtube_video_id!r}>"
        )
