"""Logging middleware for structured logging of bot interactions."""

from typing import Any, Awaitable, Callable, Dict
import uuid

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject


class LoggingMiddleware(BaseMiddleware):
    """Middleware for structured logging of bot interactions."""

    def __init__(self) -> None:
        self.logger = structlog.get_logger("bot.interactions.middleware")

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
        full_name = None
        event_type = type(event).__name__

        if isinstance(event, (Message, CallbackQuery)):
            user = event.from_user
            if user:
                user_id = user.id
                username = user.username
                full_name = user.full_name

        handler_name = getattr(handler, "__qualname__", repr(handler))

        # Create structured logger with context
        logger = self.logger.bind(
            request_id=request_id,
            user_id=user_id,
            username=username,
            full_name=full_name,
            event_type=event_type,
            handler=handler_name,
        )

        # Log incoming event
        if isinstance(event, Message):
            text_preview = (event.text or event.caption or "")[:200]
            logger.info(
                "Получено сообщение от пользователя",
                text=text_preview if text_preview else None,
                content_type=event.content_type,
            )
        elif isinstance(event, CallbackQuery):
            callback_preview = (event.data or "")[:200]
            logger.info(
                "Получено действие пользователя",
                data=callback_preview if callback_preview else None,
            )
        else:
            logger.info("Получено событие Telegram", status="обработка")

        try:
            # Process the event
            result = await handler(event, data)

            logger.info("Событие обработано успешно", status="успешно")
            return result

        except Exception as exc:
            logger.error(
                "Ошибка при обработке события",
                error=str(exc),
                exc_info=True,
            )
            raise
