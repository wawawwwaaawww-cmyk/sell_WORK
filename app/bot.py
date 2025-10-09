"""Bot initialization and configuration."""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeDefault

from app.config import settings
from app.middlewares.logging import LoggingMiddleware
from app.middlewares.user_context import UserContextMiddleware
from app.middlewares.rate_limit import RateLimitMiddleware
from app.handlers import (
    start,
    survey,
    consultation,
    payments,
    help_faq,
    admin_full as admin,
    materials,
    leads,
    product_handlers,
    user_settings,
)

logger = structlog.get_logger()

# Create bot instance
bot = Bot(
    token=settings.telegram_bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

# Create dispatcher
dp = Dispatcher()

# Register middlewares
dp.message.middleware(LoggingMiddleware())
dp.callback_query.middleware(LoggingMiddleware())
dp.message.middleware(UserContextMiddleware())
dp.callback_query.middleware(UserContextMiddleware())
dp.message.middleware(RateLimitMiddleware())
dp.callback_query.middleware(RateLimitMiddleware())

# Register handlers
# test_callbacks.register_test_handlers(dp)  # Disabled - buttons confirmed working
start.register_handlers(dp)
survey.register_handlers(dp)
consultation.register_handlers(dp)
payments.register_handlers(dp)
help_faq.register_handlers(dp)
admin.register_full_admin_handlers(dp)
materials.register_handlers(dp)
leads.register_handlers(dp)
product_handlers.register_product_handlers(dp)
user_settings.register_handlers(dp)


async def set_bot_commands() -> None:
    """Set bot commands for the menu."""
    commands = [
        BotCommand(command="start", description="Start working with the bot"),
        BotCommand(command="help", description="Show available help options"),
        BotCommand(command="reset", description="Delete my saved data"),
    ]
    
    await bot.set_my_commands(commands, BotCommandScopeDefault())
    logger.info("Bot commands set successfully")


async def set_webhook() -> None:
    """Set webhook for the bot."""
    webhook_url = f"{settings.telegram_webhook_url}{settings.webhook_path}"
    
    await bot.set_webhook(
        url=webhook_url,
        secret_token=settings.telegram_webhook_secret,
        drop_pending_updates=True,
    )
    
    logger.info("Webhook set successfully", url=webhook_url)


async def remove_webhook() -> None:
    """Remove webhook and switch to polling mode."""
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Webhook removed successfully")


async def on_startup() -> None:
    """Execute on bot startup."""
    try:
        # Set bot commands
        await set_bot_commands()
        
        # Set webhook if not in debug mode
        if not settings.debug:
            await set_webhook()
        
        logger.info("Bot started successfully", mode="webhook" if not settings.debug else "polling")
        
    except Exception as e:
        logger.error("Failed to start bot", error=str(e), exc_info=True)
        raise


async def on_shutdown() -> None:
    """Execute on bot shutdown."""
    try:
        # Remove webhook if in debug mode
        if settings.debug:
            await remove_webhook()
        
        # Close bot session
        await bot.session.close()
        
        logger.info("Bot shutdown completed")
        
    except Exception as e:
        logger.error("Error during bot shutdown", error=str(e), exc_info=True)


async def start_polling() -> None:
    """Start bot in polling mode (for development)."""
    logger.info("Starting bot in polling mode")
    
    try:
        await on_startup()
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error("Bot polling error", error=str(e), exc_info=True)
    finally:
        await on_shutdown()


if __name__ == "__main__":
    # Run bot in polling mode for development
    asyncio.run(start_polling())
