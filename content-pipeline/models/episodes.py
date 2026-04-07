"""
models/episodes.py
------------------
Core episode tracking table. Each row represents one YouTube upload
that has been detected by the polling worker.

youtube_video_id is the canonical external idempotency key.
episode_id (UUID) is the internal primary key used across all workers.

Status lifecycle:
    detected → classifying → classified → extracting_audio →
    transcribing → editing → publishing → repurposing → completed
    (or → failed | needs_classification at any step)
"""

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from models.base import Base


class EpisodeStatus(str, PyEnum):
    DETECTED = "detected"
    CLASSIFYING = "classifying"
    NEEDS_CLASSIFICATION = "needs_classification"   # Awaiting Carl's manual input
    CLASSIFIED = "classified"
    EXTRACTING_AUDIO = "extracting_audio"
    TRANSCRIBING = "transcribing"
    EDITING = "editing"
    PUBLISHING = "publishing"
    REPURPOSING = "repurposing"
    COMPLETED = "completed"
    FAILED = "failed"


class ContentPath(str, PyEnum):
    PATH_A = "path_a"   # Blog already exists; transcript reconciles against it
    PATH_B = "path_b"   # No blog; clean transcript is the canonical source
    UNKNOWN = "unknown"


class Episode(Base):
    __tablename__ = "episodes"

    episode_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # --- External identity (idempotency key) ---
    youtube_video_id: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, index=True
    )

    # --- Brand FK ---
    brand_config_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("brand_config.id"), nullable=False, index=True
    )
    brand_config: Mapped["BrandConfig"] = relationship("BrandConfig", lazy="select")  # type: ignore[name-defined]

    # --- Metadata from YouTube ---
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    youtube_published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    youtube_url: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # --- Classification ---
    content_path: Mapped[ContentPath] = mapped_column(
        Enum(ContentPath, name="content_path_enum"),
        default=ContentPath.UNKNOWN,
        nullable=False,
    )
    path_detection_method: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # 'youtube_tag' | 'clickup_lookup' | 'manual'

    # --- Pipeline state ---
    status: Mapped[EpisodeStatus] = mapped_column(
        Enum(EpisodeStatus, name="episode_status_enum"),
        default=EpisodeStatus.DETECTED,
        nullable=False,
        index=True,
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # --- Audio artifact (populated after extraction) ---
    audio_file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    audio_checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    audio_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # --- Timestamps ---
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # --- Relationships ---
    transcript: Mapped[Optional["Transcript"]] = relationship(  # type: ignore[name-defined]
        "Transcript", back_populates="episode", uselist=False, lazy="select"
    )
    outputs: Mapped[list["EpisodeOutput"]] = relationship(  # type: ignore[name-defined]
        "EpisodeOutput", back_populates="episode", lazy="select"
    )

    def __repr__(self) -> str:
        return (
            f"<Episode episode_id={self.episode_id} "
            f"yt_id={self.youtube_video_id!r} status={self.status}>"
        )
