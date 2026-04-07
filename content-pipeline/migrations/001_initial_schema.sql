-- ============================================================
-- Migration 001: Initial Schema
-- Content Pipeline - Simplicity Marketing LLC
-- ============================================================
-- NOTE: SQLAlchemy's Base.metadata.create_all() handles table
-- creation automatically at startup. This SQL file is provided
-- as a human-readable reference and for manual inspection.
-- Run it only if you need to initialize the schema outside
-- of the Python application.
-- ============================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- brand_config
-- Single source of truth for all brand-specific settings.
-- No per-brand constants may exist outside this table.
-- ============================================================
CREATE TABLE IF NOT EXISTS brand_config (
    id                          SERIAL PRIMARY KEY,
    brand_name                  VARCHAR(100) UNIQUE NOT NULL,
    youtube_channel_id          VARCHAR(50)  UNIQUE NOT NULL,
    youtube_channel_handle      VARCHAR(100),
    buzzsprout_show_id          VARCHAR(50),
    rag_domains                 TEXT,
    rag_client_scope            VARCHAR(100),
    cta_url                     VARCHAR(500),
    blog_word_count_min         INTEGER NOT NULL DEFAULT 1200,
    blog_word_count_max         INTEGER NOT NULL DEFAULT 2500,
    show_notes_word_count_min   INTEGER NOT NULL DEFAULT 300,
    show_notes_word_count_max   INTEGER NOT NULL DEFAULT 600,
    tone_descriptor             VARCHAR(300),
    wordpress_site_url          VARCHAR(500),
    wordpress_env_key_prefix    VARCHAR(100),
    is_active                   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- sync_state
-- Tracks last YouTube poll state per brand.
-- ============================================================
CREATE TABLE IF NOT EXISTS sync_state (
    id                          SERIAL PRIMARY KEY,
    brand_config_id             INTEGER UNIQUE NOT NULL REFERENCES brand_config(id),
    last_youtube_video_id       VARCHAR(20),
    last_polled_at              TIMESTAMPTZ,
    last_successful_poll_at     TIMESTAMPTZ,
    consecutive_failures        INTEGER NOT NULL DEFAULT 0,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sync_state_brand_config_id ON sync_state(brand_config_id);

-- ============================================================
-- episodes
-- One row per detected YouTube upload.
-- youtube_video_id is the external idempotency key.
-- ============================================================
CREATE TYPE IF NOT EXISTS episode_status_enum AS ENUM (
    'detected',
    'classifying',
    'needs_classification',
    'classified',
    'extracting_audio',
    'transcribing',
    'editing',
    'publishing',
    'repurposing',
    'completed',
    'failed'
);

CREATE TYPE IF NOT EXISTS content_path_enum AS ENUM (
    'path_a',
    'path_b',
    'unknown'
);

CREATE TABLE IF NOT EXISTS episodes (
    episode_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    youtube_video_id        VARCHAR(20) UNIQUE NOT NULL,
    brand_config_id         INTEGER NOT NULL REFERENCES brand_config(id),
    title                   VARCHAR(500),
    description             TEXT,
    youtube_published_at    TIMESTAMPTZ,
    youtube_url             VARCHAR(300),
    thumbnail_url           VARCHAR(500),
    duration_seconds        INTEGER,
    content_path            content_path_enum NOT NULL DEFAULT 'unknown',
    path_detection_method   VARCHAR(50),
    status                  episode_status_enum NOT NULL DEFAULT 'detected',
    last_error              TEXT,
    retry_count             INTEGER NOT NULL DEFAULT 0,
    audio_file_path         VARCHAR(500),
    audio_checksum          VARCHAR(64),
    audio_duration_seconds  INTEGER,
    detected_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at            TIMESTAMPTZ,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_episodes_youtube_video_id  ON episodes(youtube_video_id);
CREATE INDEX IF NOT EXISTS idx_episodes_brand_config_id   ON episodes(brand_config_id);
CREATE INDEX IF NOT EXISTS idx_episodes_status            ON episodes(status);

-- ============================================================
-- transcripts
-- Raw Whisper output and GPT-4o edited output.
-- Also stores Google Drive file reference.
-- ============================================================
CREATE TABLE IF NOT EXISTS transcripts (
    transcript_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    episode_id              UUID UNIQUE NOT NULL REFERENCES episodes(episode_id),
    raw_transcript          TEXT,
    clean_transcript        TEXT,
    timestamps_json         TEXT,   -- JSON array of Whisper segments
    google_drive_file_id    TEXT,
    google_drive_file_path  TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_transcripts_episode_id ON transcripts(episode_id);

-- ============================================================
-- episode_outputs
-- All derivative content outputs per episode.
-- external_id enforces idempotency vs Buzzsprout, WordPress, ClickUp.
-- ============================================================
CREATE TYPE IF NOT EXISTS output_type_enum AS ENUM (
    'podcast_episode',
    'show_notes',
    'youtube_description',
    'chapter_markers',
    'social_posts',
    'email_section',
    'quote_card_inputs',
    'blog_draft',
    'clean_transcript'
);

CREATE TYPE IF NOT EXISTS output_status_enum AS ENUM (
    'pending',
    'in_progress',
    'dark_launched',
    'completed',
    'failed',
    'skipped'
);

CREATE TABLE IF NOT EXISTS episode_outputs (
    output_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    episode_id      UUID NOT NULL REFERENCES episodes(episode_id),
    output_type     output_type_enum NOT NULL,
    status          output_status_enum NOT NULL DEFAULT 'pending',
    content         TEXT,
    external_id     TEXT,
    external_url    TEXT,
    last_error      TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_episode_outputs_episode_id   ON episode_outputs(episode_id);
CREATE INDEX IF NOT EXISTS idx_episode_outputs_output_type  ON episode_outputs(output_type);

-- ============================================================
-- dead_letter_queue
-- Permanently failed items awaiting manual review.
-- ============================================================
CREATE TABLE IF NOT EXISTS dead_letter_queue (
    dlq_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    episode_id          UUID,
    output_id           UUID,
    youtube_video_id    VARCHAR(20),
    brand_name          VARCHAR(100),
    failed_step         VARCHAR(100) NOT NULL,
    error_message       TEXT NOT NULL,
    error_traceback     TEXT,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    clickup_task_id     VARCHAR(50),
    notification_sent   BOOLEAN NOT NULL DEFAULT FALSE,
    resolved            BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at         TIMESTAMPTZ,
    resolution_notes    TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dlq_created_at   ON dead_letter_queue(created_at);
CREATE INDEX IF NOT EXISTS idx_dlq_episode_id   ON dead_letter_queue(episode_id);
CREATE INDEX IF NOT EXISTS idx_dlq_resolved     ON dead_letter_queue(resolved);
