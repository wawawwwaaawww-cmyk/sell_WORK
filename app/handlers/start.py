"""Start command and welcome flow handlers."""

import asyncio
from typing import Optional

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           Message)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.constants.start_messages import (
    DEFAULT_START_MESSAGE,
    START_MESSAGE_SETTING_KEY,
)
from app.models import User
from app.services.bonus_service import BonusService
from app.services.logging_service import ConversationLoggingService
from app.services.user_service import UserService
from app.repositories.system_settings_repository import SystemSettingsRepository
from app.utils.callbacks import Callbacks

router = Router()
logger = structlog.get_logger()


@router.message(Command("start"))
async def send_welcome(message: Message, **kwargs):
    """Handle /start command, register user, and offer a bonus."""
    try:
        session = kwargs.get("session")
        user_service = UserService(session)
        user = await user_service.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )

        conversation_logger = ConversationLoggingService(session)
        await conversation_logger.log_user_message(
            user_id=user.id,
            text=message.text or "/start",
            bot=message.bot,
            user=user,
            telegram_user=message.from_user,
            source_message=message,
        )

        repo = SystemSettingsRepository(session)
        stored_text = await repo.get_value(START_MESSAGE_SETTING_KEY, default=DEFAULT_START_MESSAGE)
        welcome_text = stored_text or DEFAULT_START_MESSAGE

        keyboard = InlineKeyboardBuilder()
        keyboard.add(
            InlineKeyboardButton(text="Получить бонус", callback_data=Callbacks.BONUS_GET)
        )
        keyboard.add(
            InlineKeyboardButton(
                text="Оставить заявку", callback_data=Callbacks.APPLICATION_START
            )
        )

        await conversation_logger.send_or_edit(
            message,
            text=welcome_text,
            user_id=user.id,
            user=user,
            reply_markup=keyboard.as_markup(),
            prefer_edit=False,
            parse_mode="HTML",
        )

        logger.info("Start command processed, bonus offered", user_id=user.id)

    except Exception as e:
        logger.error("Error in start command", error=str(e), exc_info=True)
        await message.answer("Произошла ошибка. Попробуйте еще раз позже.")


@router.callback_query(F.data == Callbacks.BONUS_GET)
async def give_bonus(callback: CallbackQuery, **kwargs):
    """Send the bonus file and kick off the conversational scenario."""
    session = kwargs.get("session")
    user = kwargs.get("user")
    conversation_logger = ConversationLoggingService(session)

    try:
        if not user:
            logger.warning("Bonus callback missing user context")
            await callback.answer("Пожалуйста, повторите позже.")
            return

        await callback.answer("🎁 Отправляю ваш бонус...")

        bonus_service = BonusService(session)
        await bonus_service.send_bonus(callback.message)
        logger.info("Bonus file sent", user_id=callback.from_user.id)

        # Wait a bit before sending the follow-up
        await asyncio.sleep(settings.bonus_followup_delay)

        opening_prompt = (
            "Бонус твой 🎁 Кстати, скажи — тебе вообще интересна тема управления деньгами? "
            "Или просто решили бонус глянуть? 🙂"
        )

        await conversation_logger.send_or_edit(
            callback.message,
            text=opening_prompt,
            user_id=user.id,
            user=user,
            metadata={"source": "sales_dialog_bonus_intro", "stage": "opening"},
            prefer_edit=False,
        )
        logger.info("Sent scripted bonus follow-up", user_id=user.id)

    except Exception as e:
        logger.error(
            "Error sending bonus file or follow-up", error=str(e), exc_info=True
        )
        await callback.message.answer(
            "Произошла ошибка. Пожалуйста, попробуйте еще раз позже."
        )


def register_handlers(dp):
    """Register start flow handlers."""
    dp.include_router(router)
