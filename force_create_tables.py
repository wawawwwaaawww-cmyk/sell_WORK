#!/usr/bin/env python3
"""Force create all database tables."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.logging_config import setup_logging

setup_logging()

import structlog

from app.db import Base, engine
import app.models  # noqa: F401 - Import to register models

logger = structlog.get_logger(__name__)


async def force_create_tables() -> None:
    """Force create all tables."""
    logger.info("🧨 Forcing table recreation")
    print("Creating tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        logger.info("🧹 Existing tables dropped")
        await conn.run_sync(Base.metadata.create_all)
        logger.info("🏗️  Tables created successfully")
    print("✅ All tables created successfully!")


if __name__ == "__main__":
    asyncio.run(force_create_tables())
