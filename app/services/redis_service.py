"""Service for managing Redis connection pool."""

import redis.asyncio as redis
import structlog
from typing import Optional

from app.config import settings

logger = structlog.get_logger(__name__)


class RedisService:
    """Singleton class to manage Redis connection."""

    _pool: Optional[redis.ConnectionPool] = None
    _client: Optional[redis.Redis] = None

    async def initialize(self) -> None:
        """Initialize the Redis connection pool."""
        if self._pool is None:
            logger.info("Initializing Redis connection pool...")
            try:
                self._pool = redis.ConnectionPool.from_url(
                    settings.redis_url,
                    max_connections=20,
                    decode_responses=True
                )
                self._client = redis.Redis(connection_pool=self._pool)
                # Ping to check connection
                await self._client.ping()
                logger.info("Redis connection pool initialized successfully.")
            except Exception as e:
                logger.error("Failed to initialize Redis connection pool", error=str(e), exc_info=True)
                self._pool = None
                self._client = None
                raise

    async def close(self) -> None:
        """Close the Redis connection pool."""
        if self._pool:
            logger.info("Closing Redis connection pool...")
            await self._pool.disconnect()
            self._pool = None
            self._client = None
            logger.info("Redis connection pool closed.")

    def get_client(self) -> Optional[redis.Redis]:
        """Get a Redis client from the pool."""
        return self._client


redis_service = RedisService()