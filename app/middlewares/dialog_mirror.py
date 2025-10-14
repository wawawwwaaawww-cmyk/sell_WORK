"""Middlewares that ensure every dialog message is mirrored to the dialogs channel."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Sequence

import structlog
from aiogram import BaseMiddleware
from aiogram.client.bot import Bot
from aiogram.client.session.middlewares.base import BaseRequestMiddleware, NextRequestMiddlewareType
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Message, TelegramObject

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import User as AppUser
from app.services.logging_service import ConversationLoggingService
from app.services.user_service import UserService


class DialogsMirrorMiddleware(BaseMiddleware):
    """Mirror incoming user messages to the dialogs channel when handlers omit explicit logging."""

    def __init__(self) -> None:
        self._logger = structlog.get_logger(__name__)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Process event and mirror message afterwards if needed."""
        try:
            return await handler(event, data)
        finally:
            await self._mirror_incoming(event, data)

    async def _mirror_incoming(self, event: TelegramObject, data: Dict[str, Any]) -> None:
        """Trigger mirroring for user messages that were not explicitly handled."""
        if not isinstance(event, Message):
            return

        # Skip when message already mirrored by a handler-specific logger
        if getattr(event, "_mirrored_to_dialogs", False):
            return

        chat = event.chat
        if chat is None or chat.type != "private":
            return

        telegram_user = event.from_user
        if telegram_user is None or telegram_user.is_bot:
            return

        session = data.get("session")
        user: Optional[AppUser] = data.get("user")
        if session is None or user is None:
            return

        try:
            bot_instance = data.get("bot") or getattr(event, "bot", None)
            conversation_logger = data.get("conversation_logger")
            if not isinstance(conversation_logger, ConversationLoggingService):
                conversation_logger = ConversationLoggingService(session)
                data["conversation_logger"] = conversation_logger

            fallback_text = event.text or event.caption or ""
            if not fallback_text:
                fallback_text = f"[{event.content_type}]"

            await conversation_logger.log_user_message(
                user_id=user.id,
                text=fallback_text,
                 metadata={"source": "auto_mirror"},
                bot=bot_instance,
                user=user,
                telegram_user=telegram_user,
                source_message=event,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.warning("auto_dialog_mirror_failed", error=str(exc))


class DialogsChannelRequestMiddleware(BaseRequestMiddleware):
    """Mirror outbound bot messages to the dialogs channel at the HTTP API layer."""

    def __init__(self, dialogs_channel_id: int) -> None:
        self._dialogs_channel_id = dialogs_channel_id
        self._logger = structlog.get_logger(__name__)

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> TelegramType:
        """Intercept bot API requests, mirror resulting messages, and return original response."""
        result = await make_request(bot, method)

        if not self._dialogs_channel_id:
            return result

        results = self._extract_messages(result)
        if not results:
            return result

        for telegram_message in results:
            await self._mirror_outbound(bot, telegram_message)

        return result

    def _extract_messages(self, result: Any) -> Sequence[Message]:
        """Return iterable of Message objects contained in the API response."""
        if isinstance(result, Message):
            return (result,)

        if isinstance(result, Iterable):
            messages: List[Message] = [item for item in result if isinstance(item, Message)]
            return tuple(messages)

        return ()

    async def _mirror_outbound(self, bot: Bot, message: Message) -> None:
        """Mirror a single outbound message if it targets a user dialog."""
        if getattr(message, "_mirrored_to_dialogs", False):
            return

        chat = message.chat
        if chat is None or chat.id == self._dialogs_channel_id or chat.type != "private":
            return

        # Ensure we do not mirror service notifications without users
        if message.from_user and message.from_user.is_bot and message.from_user.id != bot.id:
            return

        async with AsyncSessionLocal() as session:
            user_service = UserService(session)
            try:
                app_user = await user_service.repository.get_by_telegram_id(chat.id)
                if app_user is None:
                    await session.rollback()
                    return

                text_payload = message.text or message.caption or ""
                if not text_payload:
                    text_payload = f"[{message.content_type}]"

                conversation_logger = ConversationLoggingService(session)
                await conversation_logger.log_bot_message(
                    user_id=app_user.id,
                    text=text_payload,
                    bot=bot,
                    user=app_user,
                    source_message=message,
                    mirror=False,
                )
                await conversation_logger._mirror_message(
                    sender="bot",
                    bot=bot,
                    user=app_user,
                    user_id=app_user.id,
                    text=text_payload,
                    source_message=message,
                )
                await session.commit()
            except Exception as exc:  # pragma: no cover - defensive logging
                await session.rollback()
                self._logger.warning(
                    "dialogs_channel_forward_failed",
                    error=str(exc),
                    chat_id=getattr(chat, "id", None),
                )


__all__ = ["DialogsMirrorMiddleware", "DialogsChannelRequestMiddleware"]
