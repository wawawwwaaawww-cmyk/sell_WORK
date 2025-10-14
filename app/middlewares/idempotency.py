"""Middleware for update idempotency to prevent duplicate processing."""

from typing import Any, Awaitable, Callable, Dict

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from app.services.redis_service import redis_service

logger = structlog.get_logger(__name__)

class IdempotencyMiddleware(BaseMiddleware):
    """
    This middleware checks for a unique update_id in Redis to prevent
    processing the same update multiple times, e.g., after a restart.
    """

    def __init__(self):
        self.redis = redis_service.get_client()
        self.ttl_seconds = 24 * 60 * 60  # 24 hours

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        if not self.redis:
            logger.warning("Redis is not available. Idempotency check is skipped.")
            return await handler(event, data)

        idempotency_key = f"update_id:{event.update_id}"

        try:
            # The 'nx=True' argument ensures the key is set only if it does not already exist.
            # This makes the check and set operation atomic.
            if await self.redis.set(idempotency_key, "processed", ex=self.ttl_seconds, nx=True):
                # Key was set, so this is a new update
                return await handler(event, data)
            else:
                # Key already exists, this is a duplicate update
                logger.info("Duplicate update received, skipping.", update_id=event.update_id)
                return None
        except Exception as e:
            logger.error("Error during idempotency check", error=str(e), exc_info=True)
            # In case of Redis error, proceed with processing to avoid losing updates.
            return await handler(event, data)