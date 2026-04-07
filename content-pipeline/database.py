"""
database.py
-----------
SQLAlchemy engine, session factory, and initialization.
All models must be imported before calling init_db() so that
Base.metadata.create_all() picks them up.
"""

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, Session

from config import Config
from logger import get_logger

logger = get_logger(__name__)

# Module-level engine and session factory (initialized in init_db)
_engine = None
_SessionFactory = None


def init_db() -> None:
    """
    Create the SQLAlchemy engine and all tables from model metadata.
    Import all models before calling this so they register with Base.
    """
    global _engine, _SessionFactory

    if not Config.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to Railway Variables or .env"
        )

    # Import models here so Base.metadata is populated before create_all
    from models.base import Base  # noqa: F401
    import models.brand_config  # noqa: F401
    import models.episodes  # noqa: F401
    import models.transcripts  # noqa: F401
    import models.episode_outputs  # noqa: F401
    import models.sync_state  # noqa: F401
    import models.dead_letter_queue  # noqa: F401

    db_url = Config.DATABASE_URL
    # Railway Postgres URLs sometimes use postgres:// — SQLAlchemy needs postgresql://
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    _engine = create_engine(
        db_url,
        pool_pre_ping=True,       # Detect stale connections
        pool_size=5,
        max_overflow=10,
        echo=(Config.LOG_LEVEL == "DEBUG"),
    )

    # Verify connectivity before proceeding
    with _engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    logger.info("Database connection verified")

    _SessionFactory = sessionmaker(bind=_engine, autocommit=False, autoflush=False)

    # Create all tables (idempotent — skips existing tables)
    Base.metadata.create_all(_engine)
    logger.info("Database schema initialized")


def get_engine():
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """
    Context manager that provides a transactional database session.

    Usage:
        with get_session() as session:
            session.add(record)
    """
    if _SessionFactory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    session: Session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
