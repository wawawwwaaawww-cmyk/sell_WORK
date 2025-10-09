"""Help and FAQ handlers."""

from aiogram import Router, F
from app.utils.callbacks import Callbacks
from aiogram.filters import Command
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from typing import Optional

from app.handlers.scene_dispatcher import try_process_command
from app.models import User

router = Router()


@router.message(Command("help"))
async def help_command(message: Message, user: Optional[User] = None, **kwargs):
    """Handle /help command."""
    session = kwargs.get("session")
    if session and user:
        handled = await try_process_command(message, "/help", session=session, user=user)
        if handled:
            return
    help_text = """🆘 <b>Помощь по работе с ботом</b>

🚀 <b>Основные команды:</b>
• /start - Начать работу с ботом
• /help - Помощь (это сообщение)
• /admin - Панель администратора

🎯 <b>Что я умею:</b>
✅ Проводить персональную диагностику
✅ Подбирать материалы под ваши цели
✅ Записывать на консультации
✅ Отвечать на вопросы о криптовалютах
✅ Помогать выбрать подходящие курсы

💬 <b>Как со мной работать:</b>
1️⃣ Просто напишите мне свой вопрос
2️⃣ Используйте кнопки для быстрого навигации
3️⃣ Пройдите короткую анкету для лучших рекомендаций

🎆 <b>Получите максимум от общения:</b>
• Описывайте свои цели подробно
• Указывайте свой уровень опыта
• Задавайте конкретные вопросы
• Используйте кнопки для быстрых действий

🔄 Начните с команды /start чтобы получить персонализированные рекомендации!"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать с /start", callback_data="restart")],
        [InlineKeyboardButton(text="🎯 Пройти тест", callback_data=Callbacks.SURVEY_START)],
        [InlineKeyboardButton(text="📞 Консультация", callback_data=Callbacks.CONSULT_OFFER)]
    ])
    
    await message.answer(
        help_text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "restart")
async def restart_bot(callback_query):
    """Restart bot flow."""
    await callback_query.message.edit_text(
        "🚀 Отлично! Напишите /start чтобы начать сначала."
    )
    await callback_query.answer()


def register_handlers(dp):
    """Register help handlers."""
    dp.include_router(router)
