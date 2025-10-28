"""Startup script for the Telegram Sales Bot."""

import asyncio
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.logging_config import setup_logging

setup_logging()

import structlog

from app.config import settings
from app.db import init_db, close_db
from app.bot import bot, dp, remove_webhook
from app.services.scheduler_service import scheduler_service

logger = structlog.get_logger(__name__)


async def on_startup(dispatcher):
    """Execute on bot startup."""
    logger.info("🚀 Starting Telegram Sales Bot...")

    # Initialize database
    logger.info("📊 Initializing database...")
    await init_db()

    # Remove any existing webhook
    logger.info("🗑️ Removing old webhook...")
    await remove_webhook()

    # Start scheduler
    logger.info("⏰ Starting scheduler...")
    scheduler_service.start()

    logger.info("✅ Bot startup completed successfully!", debug=settings.debug, admin_ids=settings.admin_ids)


async def on_shutdown(dispatcher):
    """Execute on bot shutdown."""
    logger.info("🧹 Cleaning up...")
    scheduler_service.stop()
    await bot.session.close()
    await close_db()
    logger.info("👋 Bot stopped")


if __name__ == "__main__":
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    dp.run_polling(bot)
