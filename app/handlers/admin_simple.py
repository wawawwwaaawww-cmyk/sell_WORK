"""Simple admin handler for testing."""

import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup

from ..db import get_db
from ..repositories.admin_repository import AdminRepository
from ..models import AdminRole

logger = logging.getLogger(__name__)
router = Router()


class AdminStates(StatesGroup):
    """Admin FSM states for simple admin."""
    # Product states
    waiting_for_product_name = State()
    waiting_for_product_price = State()
    waiting_for_product_description = State()


async def admin_required(func):
    """Decorator to check if user is admin."""
    async def wrapper(message_or_query, *args, **kwargs):
        user_id = message_or_query.from_user.id
        
        async for session in get_db():
            admin_repo = AdminRepository(session)
            is_admin = await admin_repo.is_admin(user_id)
            break
            
        if not is_admin:
            if isinstance(message_or_query, Message):
                await message_or_query.answer("❌ У вас нет прав администратора.")
            else:
                await message_or_query.answer("❌ У вас нет прав администратора.", show_alert=True)
            return
        
        return await func(message_or_query, *args, **kwargs)
    return wrapper


def role_required(required_role: AdminRole):
    """Decorator to check if admin has required role."""
    def decorator(func):
        async def wrapper(message_or_query, *args, **kwargs):
            user_id = message_or_query.from_user.id
            
            async for session in get_db():
                admin_repo = AdminRepository(session)
                has_permission = await admin_repo.has_permission(user_id, required_role)
                break
                
            if not has_permission:
                if isinstance(message_or_query, Message):
                    await message_or_query.answer(f"❌ Требуется роль: {required_role.value}")
                else:
                    await message_or_query.answer(f"❌ Требуется роль: {required_role.value}", show_alert=True)
                return
            
            return await func(message_or_query, *args, **kwargs)
        return wrapper
    return decorator


@router.message(Command("admin"))
async def simple_admin_panel(message: Message):
    """Simple admin panel."""
    user_id = message.from_user.id
    
    # Check if user is admin
    async for session in get_db():
        admin_repo = AdminRepository(session)
        is_admin = await admin_repo.is_admin(user_id)
        
        if not is_admin:
            await message.answer("❌ У вас нет прав администратора.")
            return
        
        # Get capabilities
        capabilities = await admin_repo.get_admin_capabilities(user_id)
        role = capabilities.get("role", "unknown")
        break
        
    # Create simple keyboard
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Тестовая аналитика", callback_data="test_analytics")],
        [InlineKeyboardButton(text="📝 Тестовое сообщение", callback_data="test_message")]
    ])
    
    await message.answer(
        f"🔧 <b>Панель администратора (простая версия)</b>\n\n"
        f"👤 Ваша роль: <b>{role}</b>\n\n"
        f"🔍 User ID: {user_id}\n"
        f"✅ Доступ подтвержден\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "test_analytics")
async def test_analytics(callback: CallbackQuery):
    """Test analytics."""
    await callback.message.edit_text(
        "📊 <b>Тестовая аналитика</b>\n\n"
        "✅ Система работает корректно\n"
        "✅ База данных подключена\n"
        "✅ Админ-права активны\n\n"
        "Здесь будет детальная аналитика.",
        parse_mode="HTML"
    )


@router.callback_query(F.data == "test_message")
async def test_message(callback: CallbackQuery):
    """Test message."""
    await callback.answer("✅ Система полностью функциональна!", show_alert=True)


def register_simple_admin_handlers(dp):
    """Register simple admin handlers."""
    dp.include_router(router)