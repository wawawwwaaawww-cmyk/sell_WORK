"""Start command and welcome flow handlers."""

import asyncio
import os
from typing import Optional

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           Message)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.models import User
from app.services.logging_service import ConversationLoggingService
from app.services.user_service import UserService
from app.utils.callbacks import Callbacks

router = Router()
logger = structlog.get_logger()


@router.message(Command("start"))
async def start_command(message: Message, **kwargs):
    """Handle /start command and offer a bonus."""
    try:
        session = kwargs.get("session")
        user_service = UserService(session)
        user = await user_service.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )

        welcome_text = """👋 Привет!
Добро пожаловать в чат школы Азата Валеева 🎉
Здесь ты найдёшь полезные материалы, подарки и специальные предложения.
Чтобы начать — жми кнопку ниже и получи свой бонус 🎁"""

        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="Получить бонус",
            callback_data="bonus:get_file"
        ))

        await message.answer(
            welcome_text,
            reply_markup=keyboard.as_markup()
        )

        logger.info("Start command processed, bonus offered", user_id=user.id)

    except Exception as e:
        logger.error("Error in start command", error=str(e), exc_info=True)
        await message.answer("Произошла ошибка. Попробуйте еще раз позже.")


@router.callback_query(F.data == "bonus:get_file")
async def send_bonus_file(callback: CallbackQuery, **kwargs):
    """Send the bonus file and a follow-up message."""
    try:
        await callback.answer("🎁 Отправляю ваш бонус...")

        bonus_file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'bonus', 'bonus.pdf'))
        document = FSInputFile(bonus_file_path)

        await callback.message.answer_document(
            document,
            caption="Бонус текст"
        )
        logger.info("Bonus file sent", user_id=callback.from_user.id)

        await asyncio.sleep(settings.bonus_followup_delay)

        user_name = callback.from_user.first_name or "друг"
        follow_up_text = (
            f"{user_name}, хочу предложить тебе сделать следующий шаг — "
            "подобрать инструмент инвестирования, который лучше всего подойдет именно тебе. "
            "Это может быть что-то консервативное для сохранения капитала или более активное для роста. "
            "Готов начать?"
        )

        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="🤑 ДА",
            callback_data=Callbacks.SURVEY_START
        ))
        keyboard.add(InlineKeyboardButton(
            text="😞 Нет",
            callback_data="bonus:followup_no"
        ))

        await callback.message.answer(
            follow_up_text,
            reply_markup=keyboard.as_markup()
        )
        logger.info("Bonus follow-up sent", user_id=callback.from_user.id)

    except Exception as e:
        logger.error("Error sending bonus file or follow-up", error=str(e), exc_info=True)
        await callback.message.answer("Произошла ошибка. Пожалуйста, попробуйте еще раз позже.")


@router.callback_query(F.data == "bonus:followup_no")
async def handle_bonus_followup_no(callback: CallbackQuery):
    """Handle the 'No' response to the bonus follow-up."""
    try:
        await callback.answer()
        
        response_text = (
            "Понял тебя. Даже если сейчас не готов выбирать инструмент, "
            "можем просто обсудить общие стратегии — это поможет, когда придёт время. "
            "Что для тебя сейчас важнее: надёжность или возможность роста?"
        )
        
        await callback.message.edit_text(response_text)
        logger.info("User declined survey, offered strategies", user_id=callback.from_user.id)

    except Exception as e:
        logger.error("Error in bonus follow-up 'No' handler", error=str(e), exc_info=True)
        await callback.message.answer("Произошла ошибка.")


def register_handlers(dp):
    """Register start flow handlers."""
    dp.include_router(router)
