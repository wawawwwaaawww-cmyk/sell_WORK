"""Rate limiting middleware to prevent abuse."""

import time
from typing import Any, Awaitable, Callable, Dict

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from app.config import settings


class RateLimitMiddleware(BaseMiddleware):
    """Middleware for rate limiting user requests."""
    
    def __init__(self):
        self.logger = structlog.get_logger()
        self.requests = {}  # user_id -> list of timestamps
        self.max_requests = settings.rate_limit_requests
        self.window_seconds = settings.rate_limit_window
    
    def _clean_old_requests(self, user_id: int) -> None:
        """Remove old requests outside the time window."""
        current_time = time.time()
        if user_id in self.requests:
            self.requests[user_id] = [
                timestamp for timestamp in self.requests[user_id]
                if current_time - timestamp < self.window_seconds
            ]
    
    def _is_rate_limited(self, user_id: int) -> bool:
        """Check if user is rate limited."""
        current_time = time.time()
        
        # Clean old requests
        self._clean_old_requests(user_id)
        
        # Check if user has exceeded the limit
        if user_id not in self.requests:
            self.requests[user_id] = []
        
        if len(self.requests[user_id]) >= self.max_requests:
            return True
        
        # Add current request
        self.requests[user_id].append(current_time)
        return False
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Process the event with rate limiting."""
        
        # Only rate limit messages and callback queries
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)
        
        user = event.from_user
        if not user:
            return await handler(event, data)
        
        # Check rate limit
        if self._is_rate_limited(user.id):
            self.logger.warning(
                "Rate limit exceeded",
                user_id=user.id,
                username=user.username,
            )
            
            # Send rate limit message for regular messages only
            if isinstance(event, Message):
                await event.answer(
                    "⚠️ Слишком много запросов. Пожалуйста, подождите немного."
                )
            
            return None
        
        # Process the event
        return await handler(event, data)