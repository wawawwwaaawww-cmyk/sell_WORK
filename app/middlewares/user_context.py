"""User context middleware for managing user sessions."""

from typing import Any, Awaitable, Callable, Dict

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject
from sqlalchemy.sql import func

from sqlalchemy.exc import SQLAlchemyError

from app.db import AsyncSessionLocal
from app.services.user_service import UserService
from app.services.ab_testing_service import ABTestingService, ABEventType


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
                
                db_user.last_user_activity_at = func.now()

                data["user"] = db_user
                data["user_service"] = user_service

                try:
                    async with session.begin_nested():
                        ab_service = ABTestingService(session)

                        if isinstance(event, CallbackQuery) and event.message:
                            chat_id = event.message.chat.id
                            msg_id = event.message.message_id
                            assignment = await ab_service.get_assignment_by_message(chat_id, msg_id)
                            if assignment:
                                await ab_service.record_user_event(
                                    assignment.test_id,
                                    assignment.user_id,
                                    ABEventType.CLICKED,
                                    {"callback_data": event.data},
                                )

                        elif isinstance(event, Message):
                            text_value = event.text or event.caption or ""
                            if not text_value.startswith("/"):
                                await ab_service.record_event_for_latest_assignment(
                                    db_user.id,
                                    ABEventType.REPLIED,
                                    {"message_id": event.message_id},
                                )
                except SQLAlchemyError as ab_exc:  # pragma: no cover - defensive
                    self.logger.warning(
                        "user_context.ab_tracking_failed",
                        error=str(ab_exc),
                        user_id=db_user.id,
                    )
                except Exception as ab_exc:  # pragma: no cover - defensive
                    self.logger.warning(
                        "user_context.ab_tracking_failed",
                        error=str(ab_exc),
                        user_id=db_user.id,
                    )

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
