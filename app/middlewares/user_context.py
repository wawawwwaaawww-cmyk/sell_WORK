"""User context middleware for managing user sessions."""

from typing import Any, Awaitable, Callable, Dict

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from app.db import AsyncSessionLocal
from app.services.user_service import UserService


class UserContextMiddleware(BaseMiddleware):
    """Middleware for loading and managing user context."""
    
    def __init__(self):
        self.logger = structlog.get_logger()
    
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Process the event with user context."""
        
        # Only process messages and callback queries
        if not isinstance(event, (Message, CallbackQuery)):
            return await handler(event, data)
        
        user = event.from_user
        if not user:
            return await handler(event, data)
        
        # Create database session
        async with AsyncSessionLocal() as session:
            data["session"] = session
            
            try:
                # Load or create user
                user_service = UserService(session)
                db_user = await user_service.get_or_create_user(
                    telegram_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                )
                
                data["user"] = db_user
                data["user_service"] = user_service
                
                # Process the event
                result = await handler(event, data)
                
                # Commit changes
                await session.commit()
                
                return result
                
            except Exception as e:
                await session.rollback()
                self.logger.error(
                    "Error in user context middleware",
                    error=str(e),
                    user_id=user.id,
                    exc_info=True,
                )
                raise