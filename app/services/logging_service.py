"""Conversation logging, rendering, and dialog mirroring utilities."""

from __future__ import annotations

from typing import Any, Dict, Optional

import structlog
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, User as TelegramUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import User as AppUser
from app.services.user_service import UserService
from app.services.manual_dialog_service import manual_dialog_service, ManualDialogSession


class ConversationLoggingService:
    """Helper for persisting conversation history and mirroring bot dialogues."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._user_service = UserService(session)
        self._logger = structlog.get_logger(__name__)
        self._dialogs_channel_id = settings.dialogs_channel_id

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
        bot: Optional[Bot] = None,
        user: Optional[AppUser] = None,
        source_message: Optional[Message] = None,
        mirror: bool = True,
    ) -> bool:
        """Persist bot-authored message and mirror it to dialogs channel."""
        result = await self.log_message(
            user_id=user_id,
            role="bot",
            text=text,
            metadata=metadata,
        )
        if mirror:
            await self._mirror_message(
                sender="bot",
                bot=bot,
                user=user,
                user_id=user_id,
                text=text,
                source_message=source_message,
            )
        return result

    async def log_user_message(
        self,
        *,
        user_id: int,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        bot: Optional[Bot] = None,
        user: Optional[AppUser] = None,
        telegram_user: Optional[TelegramUser] = None,
        source_message: Optional[Message] = None,
    ) -> bool:
        """Persist user-authored message and mirror it to dialogs channel."""
        result = await self.log_message(
            user_id=user_id,
            role="user",
            text=text,
            metadata=metadata,
        )
        await self._mirror_message(
            sender="user",
            bot=bot,
            user=user,
            telegram_user=telegram_user,
            user_id=user_id,
            text=text,
            source_message=source_message,
        )
        return result

    async def log_manager_message(
        self,
        *,
        user_id: int,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        bot: Optional[Bot] = None,
        manager_telegram_user: Optional[TelegramUser] = None,
        source_message: Optional[Message] = None,
    ) -> bool:
        """Persist manager-authored message and mirror it to dialogs channel."""
        result = await self.log_message(
            user_id=user_id,
            role="manager",
            text=text,
            metadata=metadata,
        )
        await self._mirror_message(
            sender="manager",
            bot=bot,
            user_id=user_id,
            text=text,
            manager_telegram_user=manager_telegram_user,
            source_message=source_message,
        )
        return result

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
        user: Optional[AppUser] = None,
    ) -> Message:
        """Render response with history mode awareness, log, and mirror it."""
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

        if text:
            bot_instance = message.bot if message else None
            if self.enabled:
                await self.log_bot_message(
                    user_id=user_id,
                    text=text,
                    metadata=metadata,
                    bot=bot_instance,
                    user=user,
                    source_message=rendered,
                )
            else:
                await self._mirror_message(
                    sender="bot",
                    bot=bot_instance,
                    user_id=user_id,
                    text=text,
                    user=user,
                    source_message=rendered,
                )

        return rendered

    async def _mirror_message(
        self,
        *,
        sender: str,
        bot: Optional[Bot],
        user_id: int,
        text: str,
        user: Optional[AppUser] = None,
        telegram_user: Optional[TelegramUser] = None,
        manager_telegram_user: Optional[TelegramUser] = None,
        source_message: Optional[Message] = None,
    ) -> bool:
        """Mirror message to dialogs channel if configured."""
        if not self._dialogs_channel_id or not bot:
            return False

        try:
            target_user = user or await self._user_service.repository.get_by_id(user_id)
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.debug("mirror_user_lookup_failed", error=str(exc), user_id=user_id)
            target_user = user

        username = self._resolve_username(
            user=target_user,
            telegram_user=telegram_user,
        )

        session_info: Optional[ManualDialogSession] = manual_dialog_service.get_session_by_user(user_id)

        manager_display: Optional[str] = None
        if session_info:
            manager_display = session_info.manager_display
        elif manager_telegram_user:
            manager_display = self._resolve_manager_username(manager_telegram_user)

        header = self._build_header(sender=sender, username=username, manager_display=manager_display, manager_user=manager_telegram_user)
        reply_markup = self._build_dialog_keyboard(user_id, session_info)

        # Prioritize copying the original message to preserve formatting/media.
        if source_message is not None:
            mirrored = await self._mirror_original_message(
                bot=bot,
                source_message=source_message,
                header=header,
                reply_markup=reply_markup,
                fallback_text=text or "",
                username=username,
                sender=sender,
            )
            if mirrored and source_message is not None:
                setattr(source_message, "_mirrored_to_dialogs", True)
            return mirrored

        if not text:
            text = "[без текста]"

        relay_text = f"{header}\nТекст сообщения:\n{text}"

        mirrored = await self._send_text_mirror(
            bot=bot,
            text=relay_text,
            reply_markup=reply_markup,
            username=username,
            sender=sender,
        )
        if mirrored and source_message is not None:
            setattr(source_message, "_mirrored_to_dialogs", True)
        return mirrored

    @staticmethod
    def _resolve_username(
        *,
        user: Optional[AppUser],
        telegram_user: Optional[TelegramUser],
    ) -> str:
        """Resolve username or fallback identifier."""
        if user and getattr(user, "username", None):
            return f"@{user.username}"
        if telegram_user and telegram_user.username:
            return f"@{telegram_user.username}"

        telegram_id = None
        if user and getattr(user, "telegram_id", None):
            telegram_id = user.telegram_id
        elif telegram_user:
            telegram_id = telegram_user.id

        return f"ID {telegram_id}" if telegram_id else "неизвестный пользователь"

    @staticmethod
    def _resolve_manager_username(manager: Optional[TelegramUser]) -> str:
        """Resolve username or fallback identifier for the manager."""
        if manager and manager.username:
            return f"@{manager.username}"
        if manager:
            full_name = " ".join(filter(None, [manager.first_name, manager.last_name]))
            if full_name.strip():
                return full_name.strip()
            return f"ID {manager.id}"
        return "неизвестный менеджер"

    @staticmethod
    def _build_dialog_keyboard(
        user_id: int,
        session_info: Optional[ManualDialogSession],
    ) -> Optional[InlineKeyboardMarkup]:
        """Build inline keyboard with dialog control buttons."""
        if session_info:
            button = InlineKeyboardButton(
                text="Завершить диалог",
                callback_data=f"manual_dialog:stop:{user_id}",
            )
        else:
            button = InlineKeyboardButton(
                text="Продолжить диалог",
                callback_data=f"manual_dialog:start:{user_id}",
            )

        return InlineKeyboardMarkup(inline_keyboard=[[button]])

    def _build_header(
        self,
        *,
        sender: str,
        username: str,
        manager_display: Optional[str],
        manager_user: Optional[TelegramUser],
    ) -> str:
        """Construct header text with sender and manager data."""
        if sender == "manager":
            manager_header = manager_display or self._resolve_manager_username(manager_user)
            return f"Менеджер {manager_header} пишет пользователю {username}"

        header = (
            f"Бот пишет пользователю {username}"
            if sender == "bot"
            else f"Пользователь {username} пишет боту"
        )
        if manager_display:
            header = f"{header}\nМенеджер: {manager_display}"
        return header

    async def _mirror_original_message(
        self,
        *,
        bot: Bot,
        source_message: Message,
        header: str,
        reply_markup: Optional[InlineKeyboardMarkup],
        fallback_text: str,
        username: str,
        sender: str,
    ) -> bool:
        """Try to mirror the original Telegram message to the dialogs channel."""
        try:
            if source_message.text:
                text_body = f"{header}\nТекст сообщения:\n{source_message.text}"
                return await self._send_text_mirror(
                    bot=bot,
                    text=text_body,
                    reply_markup=reply_markup,
                    username=username,
                    sender=sender,
                )

            caption = source_message.caption or ""
            caption_block = f"{header}\n"
            if caption.strip():
                caption_block += f"{caption}"
            else:
                caption_block += f"[{source_message.content_type}]"

            await bot.copy_message(
                chat_id=self._dialogs_channel_id,
                from_chat_id=source_message.chat.id,
                message_id=source_message.message_id,
                caption=caption_block,
                reply_markup=reply_markup,
            )
            self._logger.debug(
                "dialog_mirror_sent",
                sender=sender,
                username=username,
                channel_id=self._dialogs_channel_id,
                mode="copy",
            )
            return True
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.warning(
                "dialog_mirror_copy_failed",
                error=str(exc),
                username=username,
                sender=sender,
            )
            # Fallback to text mirror with available data.
            fallback = fallback_text or f"[{source_message.content_type}]"
            return await self._send_text_mirror(
                bot=bot,
                text=f"{header}\nТекст сообщения:\n{fallback}",
                reply_markup=reply_markup,
                username=username,
                sender=sender,
            )

    async def _send_text_mirror(
        self,
        *,
        bot: Bot,
        text: str,
        reply_markup: Optional[InlineKeyboardMarkup],
        username: str,
        sender: str,
    ) -> bool:
        """Send textual mirror message to dialogs channel."""
        try:
            await bot.send_message(
                chat_id=self._dialogs_channel_id,
                text=text,
                reply_markup=reply_markup,
            )
            self._logger.debug(
                "dialog_mirror_sent",
                sender=sender,
                username=username,
                channel_id=self._dialogs_channel_id,
                mode="text",
            )
            return True
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.warning(
                "dialog_mirror_failed",
                error=str(exc),
                username=username,
                sender=sender,
            )
            return False


__all__ = ["ConversationLoggingService"]
