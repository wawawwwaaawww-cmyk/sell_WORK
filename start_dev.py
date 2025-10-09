#!/usr/bin/env python3
"""Development server startup script."""

import asyncio
import sys
from pathlib import Path

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

# Setup logging as the absolute first step before any app imports
from app.logging_config import setup_logging

setup_logging()

import structlog

from app.bot import start_polling

logger = structlog.get_logger(__name__)


if __name__ == "__main__":
    logger.info("Starting Telegram Sales Bot in development mode...")
    logger.info("Bot will use polling mode (no webhook required)")
    logger.info("Press Ctrl+C to stop the bot")

    try:
        asyncio.run(start_polling())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as exc:
        logger.error("Bot crashed", error=str(exc), exc_info=True)
        sys.exit(1)
