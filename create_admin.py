#!/usr/bin/env python3
"""Script to create the first admin user."""

import asyncio
import sys
from pathlib import Path

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

from app.logging_config import setup_logging

setup_logging()

import structlog

from app.db import AsyncSessionLocal
from app.models import Admin, AdminRole

logger = structlog.get_logger(__name__)


async def create_first_admin() -> None:
    """Create the first admin user."""
    logger.info("👑 Starting admin creation flow")
    try:
        telegram_id_raw = input("Enter your Telegram ID: ").strip()
        logger.info("📥 Received Telegram ID input", raw_value=telegram_id_raw)

        if not telegram_id_raw.isdigit():
            logger.warning("❌ Invalid Telegram ID provided", raw_value=telegram_id_raw)
            print("❌ Invalid Telegram ID. Please enter a numeric ID.")
            return

        telegram_id = int(telegram_id_raw)
        logger.info("🔐 Valid Telegram ID parsed", telegram_id=telegram_id)

        async with AsyncSessionLocal() as session:
            logger.info("🔍 Checking for existing admin", telegram_id=telegram_id)
            existing_admin = await session.get(Admin, telegram_id)
            if existing_admin:
                logger.warning("⚠️ Admin already exists", telegram_id=telegram_id)
                print(f"⚠️  Admin with ID {telegram_id} already exists!")
                return

            admin = Admin(
                telegram_id=telegram_id,
                role=AdminRole.OWNER
            )
            logger.info("🛠️ Creating admin", telegram_id=telegram_id, role=AdminRole.OWNER.value)

            session.add(admin)
            await session.commit()

            logger.info("✅ Admin created successfully", telegram_id=telegram_id)
            print("✅ Admin created successfully!")
            print(f"👤 Telegram ID: {telegram_id}")
            print(f"👑 Role: {AdminRole.OWNER.value}")
            print("📱 You can now use /admin command in the bot")

    except KeyboardInterrupt:
        logger.warning("❌ Operation cancelled by user")
        print("\n❌ Operation cancelled by user")
    except Exception as exc:
        logger.error("❌ Error creating admin", error=str(exc), exc_info=True)
        print(f"❌ Error creating admin: {exc}")


if __name__ == "__main__":
    logger.info("👑 Creating first admin user")
    print("👑 Creating first admin user...")
    print("💡 Tip: You can get your Telegram ID from @userinfobot")
    asyncio.run(create_first_admin())
