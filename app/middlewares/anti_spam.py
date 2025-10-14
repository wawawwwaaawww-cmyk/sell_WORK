"""Anti-spam middleware with progressive banning."""

import time
import json
import hashlib
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject, User

from app.config import settings
from app.services.redis_service import redis_service
from app.services.spam_notification_service import send_ban_notification
from app.logging_spam import spam_events_logger

logger = structlog.get_logger(__name__)

class AntiSpamMiddleware(BaseMiddleware):
    """
    Middleware to detect and prevent spam with a progressive ban system.
    """

    def __init__(self):
        self.redis = redis_service.get_client()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)) or not settings.spam_enabled:
            return await handler(event, data)

        user: Optional[User] = event.from_user
        if not user or user.id in settings.admin_ids_list:
            return await handler(event, data)

        if not self.redis:
            logger.warning("Redis is not available. Anti-spam is in fail-safe mode (warnings only).")
            # In fail-safe mode, we might implement a simple in-memory check or just warn.
            # For now, we'll just pass through. A warning is already logged.
            return await handler(event, data)

        # 1. Check if user is already banned
        is_banned, ban_details = await self._is_user_banned(user.id)
        if is_banned:
            # Optionally, send a single notification at the beginning of the ban.
            # This logic can be enhanced to avoid spamming the user with "you are banned" messages.
            # For now, we just drop the update.
            logger.info("Dropping update from banned user", user_id=user.id, banned_until=ban_details.get("banned_until"))
            return None

        # 2. Check spam thresholds
        reason, stats = await self._check_spam_thresholds(user.id, event)
        if reason:
            logger.warning("Spam detected", user_id=user.id, reason=reason)
            await self._apply_progressive_ban(user, reason, stats)
            return None

        return await handler(event, data)

    async def _is_user_banned(self, user_id: int) -> Tuple[bool, Dict[str, Any]]:
        """Check if a user is currently banned."""
        ban_key = f"ban:{user_id}"
        ban_data_raw = await self.redis.get(ban_key)
        if not ban_data_raw:
            return False, {}

        ban_data = json.loads(ban_data_raw)
        banned_until_ts = ban_data.get("banned_until_ts", 0)

        if time.time() < banned_until_ts:
            return True, ban_data
        
        # Ban has expired, but the key might still exist.
        return False, {}

    def _get_message_hash(self, event: TelegramObject) -> Optional[str]:
        """Generate a hash for message content to detect duplicates."""
        if isinstance(event, Message):
            if event.text:
                return hashlib.md5(event.text.encode()).hexdigest()
            if event.sticker:
                return event.sticker.file_unique_id
            if event.photo:
                return event.photo[-1].file_unique_id
            if event.video:
                return event.video.file_unique_id
            if event.voice:
                return event.voice.file_unique_id
            if event.document:
                return event.document.file_unique_id
        return None

    async def _check_spam_thresholds(self, user_id: int, event: TelegramObject) -> Tuple[Optional[str], Dict[str, int]]:
        """Check user activity against defined spam thresholds."""
        stats = {}
        # Burst check (10s)
        burst_key = f"spam:cnt10:{user_id}"
        burst_count = await self.redis.incr(burst_key)
        stats['burst10'] = burst_count
        if burst_count == 1:
            await self.redis.expire(burst_key, 10)
        if burst_count >= settings.spam_threshold_burst10:
            return "burst10", stats

        # Minute check (60s)
        minute_key = f"spam:cnt60:{user_id}"
        minute_count = await self.redis.incr(minute_key)
        stats['minute60'] = minute_count
        if minute_count == 1:
            await self.redis.expire(minute_key, 60)
        if minute_count >= settings.spam_threshold_minute60:
            return "minute60", stats

        # Duplicates check (30s)
        msg_hash = self._get_message_hash(event)
        if msg_hash:
            dupe_key = f"spam:dupe:{user_id}:{msg_hash}"
            dupe_count = await self.redis.incr(dupe_key)
            stats['dupes'] = dupe_count
            if dupe_count == 1:
                await self.redis.expire(dupe_key, 30)
            if dupe_count >= settings.spam_threshold_dupe30:
                return "dupes", stats
        
        return None, stats

    async def _apply_progressive_ban(self, user: User, reason: str, stats: Dict[str, int]) -> None:
        """Apply a progressive ban to the user."""
        ban_key = f"ban:{user.id}"
        ban_data_raw = await self.redis.get(ban_key)
        ban_data = json.loads(ban_data_raw) if ban_data_raw else {}

        # Decay logic
        last_violation_ts = ban_data.get("last_violation_ts", 0)
        if time.time() - last_violation_ts > timedelta(days=settings.spam_decay_days).total_seconds():
            ban_data["ban_level"] = 0

        ban_level = ban_data.get("ban_level", 0) + 1
        
        ban_hours = settings.spam_ban_base_hours * (settings.spam_ban_multiplier ** (ban_level - 1))
        ban_hours = min(ban_hours, settings.spam_ban_max_hours)
        
        ban_duration = timedelta(hours=ban_hours)
        banned_until = datetime.now() + ban_duration
        
        new_ban_data = {
            "ban_level": ban_level,
            "banned_until_ts": banned_until.timestamp(),
            "banned_until": banned_until.isoformat(),
            "last_violation_ts": time.time(),
            "reason": reason,
        }

        await self.redis.set(ban_key, json.dumps(new_ban_data), ex=int(ban_duration.total_seconds()) + 60) # Add a grace period

        logger.info(
            "User has been banned",
            user_id=user.id,
            username=user.username,
            ban_level=ban_level,
            ban_hours=ban_hours,
            reason=reason,
        )

        # Notify user
        try:
            await user.bot.send_message(
                user.id,
                f"Слишком частые сообщения. Доступ временно ограничен на {int(ban_hours)} ч. Попробуйте позже."
            )
        except Exception as e:
            logger.warning("Failed to notify user about ban", user_id=user.id, error=str(e))

        # Notify admins
        # Get latest stats for notification
        full_stats = {
            "burst10": await self.redis.get(f"spam:cnt10:{user.id}") or stats.get('burst10', 0),
            "minute60": await self.redis.get(f"spam:cnt60:{user.id}") or stats.get('minute60', 0),
            "dupes": stats.get('dupes', 0)
        }
        await send_ban_notification(user.bot, user, new_ban_data, full_stats)

        # Log the event
        spam_events_logger.info(json.dumps({
            "user_id": user.id,
            "username": user.username,
            "reason": reason,
            "counts": full_stats,
            "ban_level": new_ban_data['ban_level'],
            "banned_until": new_ban_data['banned_until'],
            "action": "ban"
        }))