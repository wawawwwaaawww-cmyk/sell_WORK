"""Run a comprehensive health check for the Telegram Sales Bot."""

import asyncio
import sys
from pathlib import Path

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

from app.logging_config import setup_logging

setup_logging()

import structlog

logger = structlog.get_logger(__name__)


async def check_config() -> bool:
    """Check configuration."""
    logger.info("⚙️  Checking configuration")
    print("⚙️  Checking configuration...")

    try:
        from app.config import settings

        required_settings = [
            ("TELEGRAM_BOT_TOKEN", settings.telegram_bot_token),
            ("DATABASE_URL", settings.database_url),
        ]

        missing_settings: list[str] = []
        for name, value in required_settings:
            if not value or value == f"your_{name.lower()}_here":
                logger.warning("Missing required setting", setting=name)
                missing_settings.append(name)

        if missing_settings:
            print(f"❌ Missing required settings: {', '.join(missing_settings)}")
            logger.error("❌ Missing required settings", missing_settings=missing_settings)
            return False

        print("✅ Configuration is valid")
        logger.info("✅ Configuration is valid")
        return True

    except Exception as exc:
        print(f"❌ Configuration error: {exc}")
        logger.error("❌ Configuration error", error=str(exc), exc_info=True)
        return False


async def check_database() -> bool:
    """Check database connection."""
    logger.info("🗄️  Checking database connection")
    print("🗄️  Checking database connection...")

    try:
        from app.db import AsyncSessionLocal
        from sqlalchemy import text

        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
            print("✅ Database connection successful")
            logger.info("✅ Database connection successful")
            return True

    except Exception as exc:
        print(f"❌ Database connection failed: {exc}")
        logger.error("❌ Database connection failed", error=str(exc), exc_info=True)
        return False


async def check_bot_token() -> bool:
    """Check bot token validity."""
    logger.info("🤖 Checking bot token")
    print("🤖 Checking bot token...")

    try:
        from app.bot import bot

        me = await bot.get_me()
        print(f"✅ Bot token valid - @{me.username}")
        logger.info("✅ Bot token valid", username=me.username)
        return True

    except Exception as exc:
        print(f"❌ Bot token invalid: {exc}")
        logger.error("❌ Bot token invalid", error=str(exc), exc_info=True)
        return False


async def check_imports() -> bool:
    """Check all imports."""
    logger.info("📦 Checking project imports")
    print("📦 Checking imports...")

    try:
        from app.handlers import (
            start,
            survey,
            consultation,
            payments,
            help_faq,
            admin_enhanced,
            materials,
            leads,
            product_handlers,
        )
        from app.scenes import scene_manager
        from app.repositories import (
            appointment_repository,
            product_repository,
            broadcast_repository,
            material_repository,
            admin_repository,
        )

        _ = (
            start,
            survey,
            consultation,
            payments,
            help_faq,
            admin_enhanced,
            materials,
            leads,
            product_handlers,
            scene_manager,
            appointment_repository,
            product_repository,
            broadcast_repository,
            material_repository,
            admin_repository,
        )

        print("✅ All imports successful")
        logger.info("✅ All imports successful")
        return True

    except Exception as exc:
        print(f"❌ Import error: {exc}")
        logger.error("❌ Import error", error=str(exc), exc_info=True)
        return False


async def main() -> bool:
    """Run all health checks."""
    logger.info("🩺 Starting system health check")
    print("🩺 Running system health check...")
    print("=" * 50)

    checks = [
        check_imports,
        check_config,
        check_database,
        check_bot_token,
    ]

    results: list[bool] = []
    for check in checks:
        logger.info("▶️ Executing health check", check=check.__name__)
        try:
            result = await check()
            logger.info("✅ Health check completed", check=check.__name__, result=result)
            results.append(result)
        except Exception as exc:
            logger.error("❌ Health check failed", check=check.__name__, error=str(exc), exc_info=True)
            results.append(False)
        print()

    print("=" * 50)
    if all(results):
        print("🎉 All checks passed! Bot is ready to start.")
        logger.info("🎉 All health checks passed")
        return True

    print("❌ Some checks failed. Please fix the issues before starting the bot.")
    logger.warning("❌ Some health checks failed", results=results)
    return False


if __name__ == "__main__":
    success = asyncio.run(main())
    logger.info("Health check completed", success=success)
    sys.exit(0 if success else 1)
