"""Test script for database user creation."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.logging_config import setup_logging

setup_logging()

import structlog

from app.db import AsyncSessionLocal, init_db
from app.repositories.user_repository import UserRepository

logger = structlog.get_logger(__name__)


async def main() -> None:
    """Test creating a user in the database."""
    logger.info("🧪 Starting database test")

    await init_db()
    logger.info("✅ Database initialized")
    print("✅ Database initialized")

    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)
        logger.info("👤 Checking for existing test user", telegram_id=123456789)

        try:
            existing_user = await repo.get_by_telegram_id(123456789)
            if existing_user:
                logger.info("✅ Test user already exists", user_id=existing_user.id)
                print(f"✅ User already exists with ID: {existing_user.id}")
                return

            logger.info("🆕 Creating test user")
            user = await repo.create(
                telegram_id=123456789,
                username="test_user",
                first_name="Test",
                last_name="User",
                source="test",
            )

            logger.info("✅ Test user created", user_id=user.id)
            print(f"✅ User created successfully with ID: {user.id}")
            print(f"   Telegram ID: {user.telegram_id}")
            print(f"   Name: {user.first_name} {user.last_name}")

        except Exception as exc:
            logger.error("❌ Error creating user", error=str(exc), exc_info=True)
            print(f"❌ Error creating user: {exc}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
