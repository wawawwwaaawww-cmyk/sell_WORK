#!/usr/bin/env python3
"""Webhook management script for Telegram bot."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.logging_config import setup_logging

setup_logging()

import structlog

from aiogram import Bot

from app.config import settings

logger = structlog.get_logger(__name__)


async def remove_webhook() -> None:
    """Remove webhook to enable polling mode."""
    logger.info("🧹 Removing webhook")
    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Webhook removed")
        print("✅ Webhook removed successfully. Bot can now use polling mode.")

        webhook_info = await bot.get_webhook_info()
        logger.info(
            "📊 Retrieved webhook info",
            url=webhook_info.url,
            pending_updates=webhook_info.pending_update_count,
        )
        print(f"Current webhook URL: {webhook_info.url or 'None'}")
        print(f"Pending updates: {webhook_info.pending_update_count}")

    except Exception as exc:
        logger.error("❌ Error removing webhook", error=str(exc), exc_info=True)
        print(f"❌ Error removing webhook: {exc}")
    finally:
        await bot.session.close()
        logger.info("🔚 Webhook removal session closed")


async def set_webhook(url: str) -> None:
    """Set webhook to specified URL."""
    logger.info("🔗 Setting webhook", url=url)
    bot = Bot(token=settings.telegram_bot_token)
    try:
        await bot.set_webhook(
            url=url,
            secret_token=settings.telegram_webhook_secret,
            drop_pending_updates=True,
        )
        logger.info("✅ Webhook set", url=url)
        print(f"✅ Webhook set to: {url}")

        webhook_info = await bot.get_webhook_info()
        logger.info(
            "📊 Confirmed webhook",
            active_url=webhook_info.url,
            pending_updates=webhook_info.pending_update_count,
        )
        print(f"Active webhook URL: {webhook_info.url}")
        print(f"Secret token set: {'Yes' if webhook_info.has_custom_certificate else 'No'}")

    except Exception as exc:
        logger.error("❌ Error setting webhook", error=str(exc), exc_info=True)
        print(f"❌ Error setting webhook: {exc}")
    finally:
        await bot.session.close()
        logger.info("🔚 Webhook setup session closed")


async def get_webhook_info() -> None:
    """Get current webhook information."""
    logger.info("🔎 Fetching webhook info")
    bot = Bot(token=settings.telegram_bot_token)
    try:
        webhook_info = await bot.get_webhook_info()
        logger.info(
            "📊 Current webhook info",
            url=webhook_info.url,
            pending_updates=webhook_info.pending_update_count,
            max_connections=webhook_info.max_connections,
        )
        print("📊 Current webhook info:")
        print(f"  URL: {webhook_info.url or 'None'}")
        print(f"  Pending updates: {webhook_info.pending_update_count}")
        print(f"  Max connections: {webhook_info.max_connections}")
        print(f"  Allowed updates: {webhook_info.allowed_updates or 'All'}")
        if webhook_info.last_error_date:
            print(f"  Last error: {webhook_info.last_error_message}")

    except Exception as exc:
        logger.error("❌ Error getting webhook info", error=str(exc), exc_info=True)
        print(f"❌ Error getting webhook info: {exc}")
    finally:
        await bot.session.close()
        logger.info("🔚 Webhook info session closed")


def main() -> None:
    """Main function to handle command line arguments."""
    logger.info("⚙️  Webhook manager started", args=sys.argv[1:])
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python webhook_manager.py remove     - Remove webhook (enable polling)")
        print("  python webhook_manager.py set <url>  - Set webhook to URL")
        print("  python webhook_manager.py info       - Get webhook info")
        logger.error("❌ No command provided")
        sys.exit(1)

    command = sys.argv[1].lower()
    logger.info("▶️ Executing command", command=command)

    if command == "remove":
        asyncio.run(remove_webhook())
    elif command == "set":
        if len(sys.argv) < 3:
            print("❌ Please provide webhook URL")
            logger.error("❌ Missing webhook URL for set command")
            sys.exit(1)
        url = sys.argv[2]
        asyncio.run(set_webhook(url))
    elif command == "info":
        asyncio.run(get_webhook_info())
    else:
        print(f"❌ Unknown command: {command}")
        logger.error("❌ Unknown command", command=command)
        sys.exit(1)


if __name__ == "__main__":
    main()
