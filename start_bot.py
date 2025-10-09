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
from app.bot import bot, dp, set_webhook
from app.services.scheduler_service import scheduler_service

logger = structlog.get_logger(__name__)


async def main() -> None:
    """Main startup function."""
    try:
        logger.info("🚀 Starting Telegram Sales Bot...")

        # Initialize database
        logger.info("📊 Initializing database...")
        await init_db()

        # Set webhook
        logger.info("🔗 Setting up webhook...")
        await set_webhook()

        # Start scheduler
        logger.info("⏰ Starting scheduler...")
        scheduler_service.start()

        logger.info("✅ Bot startup completed successfully!", debug=settings.debug, admin_ids=settings.admin_ids)

        # Keep the script running
        while True:
            await asyncio.sleep(60)

    except KeyboardInterrupt:
        logger.info("🛑 Shutdown requested by user")
    except Exception as exc:
        logger.error("❌ Startup error", error=str(exc), exc_info=True)
        raise
    finally:
        # Cleanup
        logger.info("🧹 Cleaning up...")
        scheduler_service.stop()
        await bot.session.close()
        await close_db()
        logger.info("👋 Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
