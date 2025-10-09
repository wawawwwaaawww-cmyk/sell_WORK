"""Logging middleware for structured logging of bot interactions."""

from typing import Any, Awaitable, Callable, Dict
import uuid

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject


class LoggingMiddleware(BaseMiddleware):
    """Middleware for structured logging of bot interactions."""
    
    def __init__(self):
        self.logger = structlog.get_logger()
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Process the event with logging."""
        
        # Generate request ID for correlation
        request_id = str(uuid.uuid4())
        data["request_id"] = request_id
        
        # Extract user info
        user_id = None
        username = None
        event_type = type(event).__name__
        
        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
            if user:
                user_id = user.id
                username = user.username
        
        # Create structured logger with context
        logger = self.logger.bind(
            request_id=request_id,
            user_id=user_id,
            username=username,
            event_type=event_type,
        )
        
        # Log incoming event
        if isinstance(event, Message):
            logger.info(
                "Incoming message",
                text=event.text[:100] if event.text else None,
                content_type=event.content_type,
            )
        elif isinstance(event, CallbackQuery):
            logger.info(
                "Incoming callback query",
                data=event.data,
            )
        
        try:
            # Process the event
            result = await handler(event, data)
            
            logger.info("Event processed successfully")
            return result
            
        except Exception as e:
            logger.error(
                "Error processing event",
                error=str(e),
                exc_info=True,
            )
            raise