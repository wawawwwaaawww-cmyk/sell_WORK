"""Development Telegram Sales Bot - Temporary Clean Version"""

import asyncio
from pathlib import Path

# Add current directory to path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from app.logging_config import setup_logging

setup_logging()

import structlog

from aiogram import Bot, Dispatcher, Router
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from pydantic_settings import BaseSettings
from pydantic import Field

logger = structlog.get_logger(__name__)

class Settings(BaseSettings):
    """Bot settings"""
    telegram_bot_token: str = Field(..., env="TELEGRAM_BOT_TOKEN")
    openai_api_key: str = Field(..., env="OPENAI_API_KEY") 
    admin_ids: str = Field(default="", env="ADMIN_IDS")
    
    class Config:
        env_file = ".env"

# Load settings
try:
    settings = Settings()
    logger.info("✅ Settings loaded successfully")
except Exception as exc:
    logger.error("❌ Error loading settings", error=str(exc), exc_info=True)
    # Fallback to direct token
    settings = type('Settings', (), {
        'telegram_bot_token': "8490095311:AAGHt_W7oO7KnaxvKEp55wvYypQbBge4LTQ",
        'openai_api_key': "your_key",
        'admin_ids': "123456789"
    })()

# Create bot and dispatcher
bot = Bot(token=settings.telegram_bot_token)
dp = Dispatcher()
router = Router()

@router.message(Command("start"))
async def start_handler(message: Message):
    """Handle /start command"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Пройти анкету", callback_data="survey_start")],
        [InlineKeyboardButton(text="📚 Получить материалы", callback_data="get_materials")],
        [InlineKeyboardButton(text="💰 Посмотреть курсы", callback_data="view_courses")]
    ])
    
    await message.answer(
        "🚀 <b>Добро пожаловать в школу криптообразования!</b>\n\n"
        "Я - ваш персональный помощник по изучению криптовалют.\n\n"
        "✅ <b>Что я умею:</b>\n"
        "• Провожу персональную диагностику вашего уровня\n"
        "• Подбираю материалы под ваши цели\n"
        "• Записываю на бесплатные консультации\n"
        "• Помогаю выбрать подходящий курс\n"
        "• Отвечаю на вопросы 24/7\n\n"
        "🎯 <b>Выберите, что вас интересует:</b>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(lambda c: c.data == "survey_start")
async def survey_start(callback: CallbackQuery):
    """Start survey"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Новичок", callback_data="level_beginner")],
        [InlineKeyboardButton(text="🟡 Базовый", callback_data="level_basic")],
        [InlineKeyboardButton(text="🟢 Продвинутый", callback_data="level_advanced")]
    ])
    
    await callback.message.edit_text(
        "🎯 <b>Анкетирование - Вопрос 1/5</b>\n\n"
        "Какой у вас уровень знаний в криптовалютах?",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(lambda c: c.data.startswith("level_"))
async def level_selected(callback: CallbackQuery):
    """Handle level selection"""
    level = callback.data.split("_")[1]
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Заработок", callback_data="goal_profit")],
        [InlineKeyboardButton(text="📚 Образование", callback_data="goal_education")],
        [InlineKeyboardButton(text="🔐 Безопасность", callback_data="goal_security")]
    ])
    
    await callback.message.edit_text(
        "🎯 <b>Анкетирование - Вопрос 2/5</b>\n\n"
        "Какая ваша основная цель изучения криптовалют?",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(lambda c: c.data.startswith("goal_"))
async def goal_selected(callback: CallbackQuery):
    """Handle goal selection"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Готов сейчас", callback_data="time_now")],
        [InlineKeyboardButton(text="⏰ В течение недели", callback_data="time_week")],
        [InlineKeyboardButton(text="📅 В течение месяца", callback_data="time_month")]
    ])
    
    await callback.message.edit_text(
        "🎯 <b>Анкетирование - Вопрос 3/5</b>\n\n"
        "Когда вы готовы начать активное изучение?",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(lambda c: c.data.startswith("time_"))
async def time_selected(callback: CallbackQuery):
    """Handle time selection"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 До 10,000 ₽", callback_data="budget_low")],
        [InlineKeyboardButton(text="💰 10,000-50,000 ₽", callback_data="budget_medium")],
        [InlineKeyboardButton(text="💎 Более 50,000 ₽", callback_data="budget_high")]
    ])
    
    await callback.message.edit_text(
        "🎯 <b>Анкетирование - Вопрос 4/5</b>\n\n"
        "Какой бюджет вы готовы инвестировать в обучение?",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(lambda c: c.data.startswith("budget_"))
async def budget_selected(callback: CallbackQuery):
    """Handle budget selection"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Телефон", callback_data="contact_phone")],
        [InlineKeyboardButton(text="✉️ Email", callback_data="contact_email")],
        [InlineKeyboardButton(text="💬 Только Telegram", callback_data="contact_telegram")]
    ])
    
    await callback.message.edit_text(
        "🎯 <b>Анкетирование - Вопрос 5/5</b>\n\n"
        "Как с вами лучше связаться для консультации?",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(lambda c: c.data.startswith("contact_"))
async def survey_complete(callback: CallbackQuery):
    """Complete survey"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Получить материалы", callback_data="get_materials")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")]
    ])
    
    await callback.message.edit_text(
        "✅ <b>Анкетирование завершено!</b>\n\n"
        "🎯 <b>Ваш профиль:</b>\n"
        "• Сегмент: ТЕПЛЫЙ 🔥\n"
        "• Уровень заинтересованности: 8/10\n"
        "• Рекомендуемый курс: 'Основы криптотрейдинга'\n\n"
        "📋 <b>Что дальше?</b>\n"
        "Я подобрал для вас персональные материалы и готов записать на бесплатную консультацию с экспертом.\n\n"
        "🎁 <b>Бонус:</b> При записи на консультацию сегодня - скидка 30% на любой курс!",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(lambda c: c.data == "get_materials")
async def get_materials(callback: CallbackQuery):
    """Send materials"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")]
    ])
    
    await callback.message.edit_text(
        "📚 <b>Ваши персональные материалы:</b>\n\n"
        "1. 📖 <b>'Основы блокчейна для начинающих'</b>\n"
        "   Понятное объяснение технологии простыми словами\n\n"
        "2. 💰 <b>'5 ошибок новичков в криптовалютах'</b>\n"
        "   Как избежать потерь при первых инвестициях\n\n"
        "3. 🛡️ <b>'Безопасность криптовалют'</b>\n"
        "   Полное руководство по защите ваших активов\n\n"
        "4. 📊 <b>'Анализ рынка криптовалют'</b>\n"
        "   Основы технического и фундаментального анализа\n\n"
        "📩 <b>Материалы отправлены вам в личные сообщения!</b>\n\n"
        "💡 <b>Совет:</b> Для получения персональных рекомендаций и разбора ваших вопросов запишитесь на бесплатную консультацию!",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(lambda c: c.data.startswith("time_"))
async def time_booked(callback: CallbackQuery):
    """Confirm booking"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Посмотреть курсы", callback_data="view_courses")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")]
    ])
    
    await callback.message.edit_text(
        "✅ <b>Консультация забронирована!</b>\n\n"
        "📅 <b>Детали встречи:</b>\n"
        "• Дата: Сегодня\n"
        "• Время: 15:00 (МСК)\n"
        "• Формат: Zoom-звонок\n"
        "• Эксперт: Александр Петров\n\n"
        "📱 <b>Что дальше:</b>\n"
        "За 15 минут до встречи вам придет ссылка на подключение\n\n"
        "🎁 <b>Специально для вас:</b>\n"
        "При покупке курса сегодня - скидка 30% + бонусные материалы стоимостью 15,000₽\n\n"
        "💡 Хотите изучить наши курсы заранее?",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(lambda c: c.data == "view_courses")
async def view_courses(callback: CallbackQuery):
    """Show courses"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 'Основы криптотрейдинга' - 24,990₽", callback_data="course_basic")],
        [InlineKeyboardButton(text="🚀 'Продвинутый трейдинг' - 49,990₽", callback_data="course_advanced")],
        [InlineKeyboardButton(text="💎 'VIP Наставничество' - 99,990₽", callback_data="course_vip")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu")]
    ])
    
    await callback.message.edit_text(
        "💰 <b>Наши образовательные программы:</b>\n\n"
        "📚 <b>1. 'Основы криптотрейдинга'</b>\n"
        "• 6 недель обучения\n"
        "• 24 практических урока\n"
        "• Личный куратор\n"
        "• Цена: 24,990₽ (со скидкой 17,493₽)\n\n"
        "🚀 <b>2. 'Продвинутый трейдинг'</b>\n"
        "• 12 недель интенсива\n"
        "• Стратегии профессионалов\n"
        "• Торговые сигналы\n"
        "• Цена: 49,990₽ (со скидкой 34,993₽)\n\n"
        "💎 <b>3. 'VIP Наставничество'</b>\n"
        "• Индивидуальная программа\n"
        "• Личный ментор\n"
        "• Разбор ваших сделок\n"
        "• Цена: 99,990₽ (со скидкой 69,993₽)\n\n"
        "🎁 <b>Скидка действует только сегодня!</b>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(lambda c: c.data.startswith("course_"))
async def course_selected(callback: CallbackQuery):
    """Handle course selection"""
    course_map = {
        "course_basic": ("Основы криптотрейдинга", "24,990₽", "17,493₽"),
        "course_advanced": ("Продвинутый трейдинг", "49,990₽", "34,993₽"),
        "course_vip": ("VIP Наставничество", "99,990₽", "69,993₽")
    }
    
    course_name, original_price, discount_price = course_map.get(callback.data, ("Курс", "0₽", "0₽"))
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить курс", url="https://payment.example.com/pay")],
        [InlineKeyboardButton(text="❓ Задать вопрос", callback_data="ask_question")],
        [InlineKeyboardButton(text="🔙 К курсам", callback_data="view_courses")]
    ])
    
    await callback.message.edit_text(
        f"📚 <b>'{course_name}'</b>\n\n"
        f"💰 <b>Стоимость:</b>\n"
        f"• Обычная цена: ~~{original_price}~~\n"
        f"• Ваша цена: <b>{discount_price}</b>\n"
        f"• Экономия: 30%\n\n"
        f"🎁 <b>Бонусы при покупке сегодня:</b>\n"
        f"• Доступ к закрытому чату выпускников\n"
        f"• Бонусные материалы на 15,000₽\n"
        f"• 3 месяца бесплатных торговых сигналов\n"
        f"• Сертификат о прохождении курса\n\n"
        f"⚠️ <b>Важно:</b> Обучение и инвестиции связаны с рисками. Мы не гарантируем прибыль, но даем знания для принятия обоснованных решений.\n\n"
        f"✅ <b>100% гарантия возврата в течение 14 дней</b>",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(lambda c: c.data == "main_menu")
async def main_menu(callback: CallbackQuery):
    """Return to main menu"""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Пройти анкету", callback_data="survey_start")],
        [InlineKeyboardButton(text="📚 Получить материалы", callback_data="get_materials")],
        [InlineKeyboardButton(text="💰 Посмотреть курсы", callback_data="view_courses")]
    ])
    
    await callback.message.edit_text(
        "🏠 <b>Главное меню</b>\n\n"
        "Выберите интересующий вас раздел:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

# Removed echo handler - will be replaced with LLM integration

# Register router
dp.include_router(router)

async def main():
    """Main function"""
    print("🚀 Starting Telegram Sales Bot in Virtual Environment...")
    print(f"🐍 Python version: {sys.version}")
    print(f"📂 Working directory: {Path.cwd()}")
    print(f"🤖 Bot token: {settings.telegram_bot_token[:10]}...")
    print("✅ All systems ready!")
    
    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        print("\n🛑 Bot stopped by user")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())