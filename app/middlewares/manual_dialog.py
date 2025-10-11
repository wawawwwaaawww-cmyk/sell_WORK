"""Middleware that routes messages during manual dialog sessions."""

from typing import Any, Awaitable, Callable, Dict, Optional

import structlog
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import Message, TelegramObject

from app.models import User as AppUser
from app.services.logging_service import ConversationLoggingService
from app.services.manual_dialog_service import manual_dialog_service, ManualDialogSession


class ManualDialogMiddleware(BaseMiddleware):
    """Intercept messages when manual dialog mode is active."""

    def __init__(self) -> None:
        self._logger = structlog.get_logger(__name__)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """Process message events with manual dialog routing."""
        if not isinstance(event, Message):
            return await handler(event, data)

        user: Optional[AppUser] = data.get("user")
        session = data.get("session")

        # Route messages from managers first
        manager_session = manual_dialog_service.get_session_by_manager(event.from_user.id) if event.from_user else None
        if manager_session:
            processed = await self._forward_manager_message(event, manager_session, session)
            if processed:
                return None

        if not user:
            return await handler(event, data)

        user_session = manual_dialog_service.get_session_by_user(user.id)
        if not user_session:
            return await handler(event, data)

        processed = await self._forward_user_message(event, user_session, session, user)
        if processed:
            return None

        return await handler(event, data)

    async def _forward_user_message(
        self,
        message: Message,
        dialog_session: ManualDialogSession,
        db_session: Any,
        user: AppUser,
    ) -> bool:
        """Forward user messages to the active manager."""
        text_payload = message.text or message.caption or ""
        if db_session:
            logger = ConversationLoggingService(db_session)
            await logger.log_user_message(
                user_id=user.id,
                text=text_payload or f"[{message.content_type}]",
                metadata={"source": "manual_dialog"},
                bot=message.bot,
                user=user,
                telegram_user=message.from_user,
                source_message=message,
            )

        if not dialog_session.manager_telegram_id:
            return True

        try:
            await message.bot.copy_message(
                chat_id=dialog_session.manager_telegram_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
        except TelegramForbiddenError:
            self._logger.warning(
                "manual_manager_chat_forbidden",
                manager_id=dialog_session.manager_telegram_id,
                user_id=user.id,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.warning(
                "manual_forward_user_failed",
                error=str(exc),
                manager_id=dialog_session.manager_telegram_id,
                user_id=user.id,
            )
        return True

    async def _forward_manager_message(
        self,
        message: Message,
        dialog_session: ManualDialogSession,
        db_session: Any,
    ) -> bool:
        """Forward manager messages to the user they currently handle."""
        target_chat_id = dialog_session.user_telegram_id
        try:
            await message.bot.copy_message(
                chat_id=target_chat_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
        except TelegramForbiddenError:
            self._logger.warning(
                "manual_user_chat_forbidden",
                user_telegram_id=target_chat_id,
                manager_id=dialog_session.manager_telegram_id,
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.warning(
                "manual_forward_manager_failed",
                error=str(exc),
                user_telegram_id=target_chat_id,
                manager_id=dialog_session.manager_telegram_id,
            )

        text_payload = message.text or message.caption or ""
        if db_session:
            logger = ConversationLoggingService(db_session)
            await logger.log_manager_message(
                user_id=dialog_session.user_id,
                text=text_payload or f"[{message.content_type}]",
                metadata={"source": "manual_dialog"},
                bot=message.bot,
                manager_telegram_user=message.from_user,
                source_message=message,
            )

        return True


__all__ = ["ManualDialogMiddleware"]
