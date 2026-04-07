"""
models/brand_config.py
----------------------
Single source of truth for all brand-specific settings.
Every worker resolves its settings from this table.
No per-brand constants may be hardcoded outside this model.

Required columns (per architecture spec §8):
    brand_name, youtube_channel_id, buzzsprout_show_id,
    rag_domains, rag_client_scope, cta_url,
    blog_word_count_min, blog_word_count_max,
    show_notes_word_count_min, show_notes_word_count_max,
    tone_descriptor
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class BrandConfig(Base):
    __tablename__ = "brand_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- Identity ---
    brand_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    youtube_channel_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    youtube_channel_handle: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # --- Podcast ---
    # Buzzsprout show IDs are per-brand; token is global (stored in env)
    buzzsprout_show_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # --- RAG / AI ---
    # Comma-separated domain tags used to filter RAG context per brand
    rag_domains: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Scope label used to restrict GPT-4o editing to the correct client context
    rag_client_scope: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # --- Content Settings ---
    cta_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    blog_word_count_min: Mapped[int] = mapped_column(Integer, default=1200, nullable=False)
    blog_word_count_max: Mapped[int] = mapped_column(Integer, default=2500, nullable=False)
    show_notes_word_count_min: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    show_notes_word_count_max: Mapped[int] = mapped_column(Integer, default=600, nullable=False)
    tone_descriptor: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    # --- WordPress (Phase 3+) ---
    wordpress_site_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # WordPress credentials are brand-specific; stored as env var references
    wordpress_env_key_prefix: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # --- Status ---
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
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

    def rag_domains_list(self) -> list[str]:
        """Return rag_domains as a parsed list."""
        if not self.rag_domains:
            return []
        return [d.strip() for d in self.rag_domains.split(",") if d.strip()]

    def __repr__(self) -> str:
        return f"<BrandConfig brand={self.brand_name!r} channel={self.youtube_channel_id!r}>"
