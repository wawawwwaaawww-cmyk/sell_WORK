"""Conversation logging and message rendering utilities."""

from __future__ import annotations

from typing import Any, Dict, Optional

import structlog
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.user_service import UserService


class ConversationLoggingService:
    """Helper for persisting conversation history and rendering bot replies."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._user_service = UserService(session)
        self._logger = structlog.get_logger(__name__)

    @property
    def enabled(self) -> bool:
        """Check if conversation logging is enabled in settings."""
        return settings.conversation_logging_enabled

    @property
    def allow_message_editing(self) -> bool:
        """Return True when current mode permits editing existing messages."""
        return settings.allow_message_editing

    async def log_message(
        self,
        *,
        user_id: int,
        role: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Persist arbitrary message to the history table."""
        if not self.enabled or not user_id or not text:
            return False

        try:
            return await self._user_service.save_message(
                user_id=user_id,
                role=role,
                text=text,
                metadata=metadata or {},
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.warning(
                "conversation_logging_failed",
                error=str(exc),
                user_id=user_id,
            )
            return False

    async def log_bot_message(
        self,
        *,
        user_id: int,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Persist bot-authored message."""
        return await self.log_message(
            user_id=user_id,
            role="bot",
            text=text,
            metadata=metadata,
        )

    async def log_user_message(
        self,
        *,
        user_id: int,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Persist user-authored message."""
        return await self.log_message(
            user_id=user_id,
            role="user",
            text=text,
            metadata=metadata,
        )

    async def send_or_edit(
        self,
        message: Message,
        *,
        text: str,
        user_id: int,
        reply_markup: Optional[Any] = None,
        parse_mode: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        prefer_edit: bool = True,
    ) -> Message:
        """Render response with history mode awareness and log it."""
        if message is None:
            raise ValueError("message is required for send_or_edit")

        send_kwargs: Dict[str, Any] = {}
        if reply_markup is not None:
            send_kwargs["reply_markup"] = reply_markup
        if parse_mode:
            send_kwargs["parse_mode"] = parse_mode

        rendered: Optional[Message] = None
        if prefer_edit and self.allow_message_editing:
            try:
                rendered = await message.edit_text(text, **send_kwargs)
            except Exception as exc:  # pragma: no cover - Telegram formatting issues
                self._logger.debug("message_edit_skipped", error=str(exc))
                rendered = None

        if rendered is None:
            rendered = await message.answer(text, **send_kwargs)

        if self.enabled and text:
            await self.log_bot_message(
                user_id=user_id,
                text=text,
                metadata=metadata,
            )

        return rendered


__all__ = ["ConversationLoggingService"]
