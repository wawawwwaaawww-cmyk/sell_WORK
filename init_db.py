#!/usr/bin/env python3
"""Database initialization script."""

import asyncio
import sys
from pathlib import Path

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

from app.logging_config import setup_logging

setup_logging()

import structlog
from urllib.parse import urlparse, urlunparse

from app import models  # noqa: F401 - ensure models are registered
from app.config import settings
from app.db import create_tables, init_db

logger = structlog.get_logger(__name__)


def _mask_database_url(url: str) -> str:
    """Mask sensitive credentials in the database URL for logging."""
    parsed = urlparse(url)
    netloc = parsed.netloc
    if parsed.password:
        netloc = netloc.replace(parsed.password, "***")
    masked = parsed._replace(netloc=netloc)
    return urlunparse(masked)


async def initialize_database() -> None:
    """Initialize database with all tables."""
    logger.info("🔧 Initializing database")
    try:
        await init_db()
        logger.info("✅ Database connection established")

        await create_tables()
        logger.info("✅ All tables created successfully")

        logger.info("🎉 Database initialization completed")
    except Exception as exc:
        logger.error("❌ Database initialization failed", error=str(exc), exc_info=True)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    logger.info(
        "🚀 Starting database initialization",
        database_url=_mask_database_url(settings.database_url),
    )
    asyncio.run(initialize_database())
