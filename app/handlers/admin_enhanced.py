"""Enhanced admin handlers with broadcast, A/B testing, and content management."""

import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import json

from aiogram import Router, F, Dispatcher
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, desc
from sqlalchemy.orm import selectinload

from ..db import get_db
from ..models import (
    User, Lead, Event, Payment, ABTest, ABVariant, ABResult, Broadcast, Material,
    UserSegment, ABTestStatus, ABTestMetric, MaterialType, AdminRole
)
from ..repositories.broadcast_repository import BroadcastRepository, ABTestRepository
from ..repositories.admin_repository import AdminRepository
from ..repositories.product_repository import ProductRepository
from ..services.materials_service import MaterialService
from ..services.excel_material_service import excel_material_service
from ..services.scheduler_service import scheduler_service
from ..config import settings

logger = logging.getLogger(__name__)
router = Router()


class AdminStates(StatesGroup):
    # Broadcast states
    waiting_for_broadcast_text = State()
    waiting_for_broadcast_buttons = State()
    
    # A/B test states
    waiting_for_ab_test_name = State()
    waiting_for_ab_test_population = State()
    waiting_for_ab_test_variant_a = State()
    waiting_for_ab_test_variant_b = State()
    
    # Material states
    waiting_for_material_title = State()
    waiting_for_material_content = State()
    waiting_for_material_url = State()
    waiting_for_material_tags = State()
    
    # Product states
    waiting_for_product_name = State()
    waiting_for_product_price = State()
    waiting_for_product_description = State()

   # Excel materials states
   waiting_for_excel_file = State()
   waiting_for_media_files = State()
   waiting_for_test_username = State()


def admin_required(func):
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
        
        # Filter out problematic kwargs passed by middleware
        clean_kwargs = {k: v for k, v in kwargs.items() if k not in ['dispatcher', 'bot', 'session']}
        return await func(message_or_query, *args, **clean_kwargs)
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
            
            # Filter out problematic kwargs passed by middleware
            clean_kwargs = {k: v for k, v in kwargs.items() if k not in ['dispatcher', 'bot', 'session']}
            return await func(message_or_query, *args, **clean_kwargs)
        return wrapper
    return decorator


@router.message(Command("admin"))
@admin_required
async def admin_panel(message: Message):
    """Show enhanced admin panel."""
    async for session in get_db():
        admin_repo = AdminRepository(session)
        capabilities = await admin_repo.get_admin_capabilities(message.from_user.id)
        break
        
        buttons = []
        
        # Analytics (all admins)
        buttons.append([InlineKeyboardButton(text="📊 Аналитика", callback_data="admin_analytics")])
        
        # Leads management (all admins)
        buttons.append([InlineKeyboardButton(text="👥 Лиды", callback_data="admin_leads")])
        
        # Content management (editors and above)
        if capabilities.get("can_manage_broadcasts"):
            buttons.append([
                InlineKeyboardButton(text="📢 Рассылки", callback_data="admin_broadcasts"),
                InlineKeyboardButton(text="🧪 A/B тесты", callback_data="admin_ab_tests")
            ])
            buttons.append([
                InlineKeyboardButton(text="📚 Материалы", callback_data="admin_materials"),
                InlineKeyboardButton(text="💰 Продукты", callback_data="admin_products")
            ])
        
        # User management (admins and above)
        if capabilities.get("can_manage_users"):
            buttons.append([InlineKeyboardButton(text="👤 Пользователи", callback_data="admin_users")])
        
        # Payment management (admins and above)
        if capabilities.get("can_manage_payments"):
            buttons.append([InlineKeyboardButton(text="💳 Платежи", callback_data="admin_payments")])
        
        # Admin management (owners only)
        if capabilities.get("can_manage_admins"):
            buttons.append([InlineKeyboardButton(text="⚙️ Админы", callback_data="admin_admins")])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        role = capabilities.get("role", "unknown")
        
        await message.answer(
            f"🔧 <b>Панель администратора</b>\n\n"
            f"👤 Ваша роль: <b>{role}</b>\n\n"
            "Выберите нужный раздел:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )


# Enhanced Analytics
@router.callback_query(F.data == "admin_analytics")
@admin_required
async def show_analytics(callback: CallbackQuery, **kwargs):
    """Show comprehensive analytics."""
    try:
        async for session in get_db():
            # Users stats
            total_users_result = await session.execute(select(func.count(User.id)))
            total_users = total_users_result.scalar()
            
            week_ago = datetime.utcnow() - timedelta(days=7)
            active_users_result = await session.execute(
                select(func.count(User.id)).where(User.updated_at > week_ago)
            )
            active_users = active_users_result.scalar()
            
            # Segment distribution
            cold_users_result = await session.execute(
                select(func.count(User.id)).where(User.segment == UserSegment.COLD)
            )
            cold_users = cold_users_result.scalar()
            
            warm_users_result = await session.execute(
                select(func.count(User.id)).where(User.segment == UserSegment.WARM)
            )
            warm_users = warm_users_result.scalar()
            
            hot_users_result = await session.execute(
                select(func.count(User.id)).where(User.segment == UserSegment.HOT)
            )
            hot_users = hot_users_result.scalar()
            
            # Payments stats
            total_payments_result = await session.execute(select(func.count(Payment.id)))
            total_payments = total_payments_result.scalar()
            
            successful_payments_result = await session.execute(
                select(func.count(Payment.id)).where(Payment.status == "paid")
            )
            successful_payments = successful_payments_result.scalar()
            
            total_revenue_result = await session.execute(
                select(func.sum(Payment.amount)).where(Payment.status == "paid")
            )
            total_revenue = total_revenue_result.scalar() or 0
            break
            
            stats_text = f"""📊 <b>Аналитика системы</b>

👥 <b>Пользователи:</b>
• Всего: {total_users}
• Активные за неделю: {active_users}

🎯 <b>Сегменты:</b>
• ❄️ Холодные: {cold_users}
• 🔥 Тёплые: {warm_users}
• 🌶️ Горячие: {hot_users}

💳 <b>Платежи:</b>
• Всего: {total_payments}
• Успешные: {successful_payments}
• Конверсия: {(successful_payments/max(total_payments,1)*100):.1f}%
• Выручка: {total_revenue:,.0f} ₽"""
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_analytics")],
                [InlineKeyboardButton(text="📈 Детальная аналитика", callback_data="admin_detailed_analytics")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
            ])
            
            await callback.message.edit_text(stats_text, reply_markup=keyboard, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Error showing analytics: {e}")
        await callback.answer("❌ Ошибка при загрузке аналитики", show_alert=True)


# Enhanced Broadcast Management
@router.callback_query(F.data == "admin_broadcasts")
@role_required(AdminRole.EDITOR)
async def broadcast_management(callback: CallbackQuery, **kwargs):
    """Enhanced broadcast management menu."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Новая рассылка", callback_data="broadcast_create")],
        [InlineKeyboardButton(text="📊 История рассылок", callback_data="broadcast_history")],
        [InlineKeyboardButton(text="🎯 Сегменты пользователей", callback_data="broadcast_segments")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(
        "📢 <b>Управление рассылками</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "broadcast_create")
@role_required(AdminRole.EDITOR)
async def broadcast_create_step1(callback: CallbackQuery, state: FSMContext, **kwargs):
    """Start creating new broadcast - step 1: get text."""
    await state.set_state(AdminStates.waiting_for_broadcast_text)
    await callback.message.edit_text(
        "📝 <b>Новая рассылка</b>\n\n"
        "Шаг 1/3: Отправьте текст сообщения.\n\n"
        "📝 Можно использовать:\n"
        "• <b>Жирный текст</b>\n"
        "• <i>Курсив</i>\n"
        "• <code>Моноширинный текст</code>\n"
        "• Эмодзи 🚀",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_broadcast_text)
@role_required(AdminRole.EDITOR)
async def broadcast_create_step2(message: Message, state: FSMContext, **kwargs):
    """Step 2: Select target segment."""
    broadcast_text = message.text
    await state.update_data(broadcast_text=broadcast_text)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Все пользователи", callback_data="broadcast_segment_all")],
        [InlineKeyboardButton(text="❄️ Холодные (0-5 баллов)", callback_data="broadcast_segment_cold")],
        [InlineKeyboardButton(text="🔥 Тёплые (6-10 баллов)", callback_data="broadcast_segment_warm")],
        [InlineKeyboardButton(text="🌶️ Горячие (11+ баллов)", callback_data="broadcast_segment_hot")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcasts")]
    ])
    
    preview_text = broadcast_text[:200] + "..." if len(broadcast_text) > 200 else broadcast_text
    
    await message.answer(
        f"📝 <b>Превью сообщения:</b>\n\n{preview_text}\n\n"
        f"🎯 <b>Шаг 2/3:</b> Выберите целевую аудиторию:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("broadcast_segment_"))
@role_required(AdminRole.EDITOR)
async def broadcast_create_step3(callback: CallbackQuery, state: FSMContext):
    """Step 3: Final confirmation and send."""
    segment = callback.data.split("_")[2]
    await state.update_data(target_segment=segment)
    
    data = await state.get_data()
    broadcast_text = data.get("broadcast_text")
    
    async with get_db() as session:
        broadcast_repo = BroadcastRepository(session)
        
        # Get target user count
        segment_filter = None if segment == "all" else {"segments": [segment]}
        target_users = await broadcast_repo.get_target_users_for_broadcast(segment_filter)
        user_count = len(target_users)
        
        segment_names = {
            "all": "👥 Все пользователи",
            "cold": "❄️ Холодные",
            "warm": "🔥 Тёплые",
            "hot": "🌶️ Горячие"
        }
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить сейчас", callback_data="broadcast_send_now")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcasts")]
        ])
        
        preview_text = broadcast_text[:300] + "..." if len(broadcast_text) > 300 else broadcast_text
        
        await callback.message.edit_text(
            f"📩 <b>Подтверждение отправки</b>\n\n"
            f"📝 <b>Сообщение:</b>\n{preview_text}\n\n"
            f"🎯 <b>Аудитория:</b> {segment_names.get(segment, segment)}\n"
            f"👥 <b>Количество получателей:</b> {user_count}\n\n"
            "❗️ Подтвердите отправку:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )


@router.callback_query(F.data == "broadcast_send_now")
@role_required(AdminRole.EDITOR)
async def broadcast_send(callback: CallbackQuery, state: FSMContext):
    """Send broadcast now."""
    try:
        data = await state.get_data()
        broadcast_text = data.get("broadcast_text")
        target_segment = data.get("target_segment")
        
        async with get_db() as session:
            broadcast_repo = BroadcastRepository(session)
            
            # Create broadcast record
            segment_filter = None if target_segment == "all" else {"segments": [target_segment]}
            broadcast = await broadcast_repo.create_broadcast(
                title=f"Рассылка {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                body=broadcast_text,
                segment_filter=segment_filter
            )
            
            # Get target users
            target_users = await broadcast_repo.get_target_users_for_broadcast(segment_filter)
            
            await session.commit()
            
            # Implement actual sending logic
            from ..bot import bot
            from ..services.broadcast_service import BroadcastService
            
            broadcast_service = BroadcastService(session)
            
            # Parse buttons if provided
            buttons_markup = None
            if broadcast.buttons:
                try:
                    import json
                    buttons_data = json.loads(broadcast.buttons)
                    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                    
                    keyboard_buttons = []
                    for button in buttons_data:
                        if 'text' in button and 'url' in button:
                            keyboard_buttons.append([
                                InlineKeyboardButton(text=button['text'], url=button['url'])
                            ])
                    
                    if keyboard_buttons:
                        buttons_markup = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
                except Exception as e:
                    logger.error(f"Error parsing buttons: {e}")
            
            # Send broadcast to all target users
            sent_count = 0
            failed_count = 0
            
            for user in target_users:
                try:
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=broadcast.content,
                        reply_markup=buttons_markup,
                        parse_mode="HTML"
                    )
                    sent_count += 1
                    
                    # Update broadcast statistics
                    await broadcast_repo.mark_as_sent(broadcast.id, user.id)
                    
                    # Rate limiting - Telegram allows 30 messages per second
                    import asyncio
                    await asyncio.sleep(0.04)  # ~25 messages per second to be safe
                    
                except Exception as e:
                    logger.error(f"Failed to send broadcast to user {user.id}: {e}")
                    failed_count += 1
                    await broadcast_repo.mark_as_failed(broadcast.id, user.id)
            
            # Update broadcast status
            broadcast.status = "completed"
            broadcast.sent_count = sent_count
            broadcast.failed_count = failed_count
            
            await callback.message.edit_text(
                f"✅ <b>Рассылка запущена!</b>\n\n"
                f"🆔 ID: {broadcast.id}\n"
                f"👥 Получателей: {len(target_users)}\n"
                f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                parse_mode="HTML"
            )
            
            await state.clear()
            
    except Exception as e:
        logger.error(f"Error sending broadcast: {e}")
        await callback.answer("❌ Ошибка при отправке рассылки", show_alert=True)


# A/B Testing Management
@router.callback_query(F.data == "admin_ab_tests")
@role_required(AdminRole.EDITOR)
async def ab_tests_management(callback: CallbackQuery):
    """A/B tests management menu."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Новый A/B тест", callback_data="ab_test_create")],
        [InlineKeyboardButton(text="📊 Активные тесты", callback_data="ab_test_active")],
        [InlineKeyboardButton(text="📈 Результаты", callback_data="ab_test_results")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(
        "🧪 <b>A/B тестирование</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "ab_test_create")
@role_required(AdminRole.EDITOR)
async def ab_test_create_step1(callback: CallbackQuery, state: FSMContext):
    """Create A/B test - step 1: name."""
    await state.set_state(AdminStates.waiting_for_ab_test_name)
    await callback.message.edit_text(
        "🧪 <b>Новый A/B тест</b>\n\n"
        "Шаг 1/4: Введите название теста:",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_ab_test_name)
@role_required(AdminRole.EDITOR)
async def ab_test_create_step2(message: Message, state: FSMContext):
    """Create A/B test - step 2: population."""
    test_name = message.text.strip()
    
    if len(test_name) < 3:
        await message.answer("❌ Название должно содержать минимум 3 символа. Попробуйте снова:")
        return
    
    await state.update_data(test_name=test_name)
    await state.set_state(AdminStates.waiting_for_ab_test_population)
    
    await message.answer(
        f"✅ Название: <b>{test_name}</b>\n\n"
        "Шаг 2/4: Введите процент пользователей для теста (10-100):",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_ab_test_population)
@role_required(AdminRole.EDITOR)
async def ab_test_create_step3(message: Message, state: FSMContext):
    """Create A/B test - step 3: variant A."""
    try:
        population = int(message.text.strip())
        if not (10 <= population <= 100):
            raise ValueError("Invalid range")
    except ValueError:
        await message.answer("❌ Введите число от 10 до 100. Попробуйте снова:")
        return
    
    await state.update_data(population=population)
    await state.set_state(AdminStates.waiting_for_ab_test_variant_a)
    
    await message.answer(
        f"✅ Охват: <b>{population}%</b> пользователей\n\n"
        "Шаг 3/4: Введите текст варианта A:",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_ab_test_variant_a)
@role_required(AdminRole.EDITOR)
async def ab_test_create_step4(message: Message, state: FSMContext):
    """Create A/B test - step 4: variant B."""
    variant_a = message.text.strip()
    
    if len(variant_a) < 10:
        await message.answer("❌ Текст варианта A должен содержать минимум 10 символов. Попробуйте снова:")
        return
    
    await state.update_data(variant_a=variant_a)
    await state.set_state(AdminStates.waiting_for_ab_test_variant_b)
    
    preview_a = variant_a[:100] + "..." if len(variant_a) > 100 else variant_a
    
    await message.answer(
        f"✅ Вариант A: <i>{preview_a}</i>\n\n"
        "Шаг 4/4: Введите текст варианта B:",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_ab_test_variant_b)
@role_required(AdminRole.EDITOR)
async def ab_test_create_final(message: Message, state: FSMContext):
    """Create A/B test - final step: save test."""
    variant_b = message.text.strip()
    
    if len(variant_b) < 10:
        await message.answer("❌ Текст варианта B должен содержать минимум 10 символов. Попробуйте снова:")
        return
    
    # Get all data from state
    data = await state.get_data()
    test_name = data['test_name']
    population = data['population']
    variant_a = data['variant_a']
    
    try:
        async with get_db() as session:
            from ..repositories.broadcast_repository import ABTestRepository
            from ..models import ABTestMetric
            
            ab_test_repo = ABTestRepository(session)
            
            # Create A/B test
            ab_test = await ab_test_repo.create_ab_test(
                name=test_name,
                population=population,
                metric=ABTestMetric.CTR  # Default to CTR
            )
            
            # Create variant A
            await ab_test_repo.create_ab_variant(
                ab_test_id=ab_test.id,
                variant_code="A",
                title=f"{test_name} - Вариант A",
                body=variant_a,
                weight=50
            )
            
            # Create variant B
            await ab_test_repo.create_ab_variant(
                ab_test_id=ab_test.id,
                variant_code="B",
                title=f"{test_name} - Вариант B",
                body=variant_b,
                weight=50
            )
            
            await session.commit()
            
            preview_a = variant_a[:100] + "..." if len(variant_a) > 100 else variant_a
            preview_b = variant_b[:100] + "..." if len(variant_b) > 100 else variant_b
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="▶️ Запустить тест", callback_data=f"ab_test_start:{ab_test.id}")],
                [InlineKeyboardButton(text="⬅️ К списку тестов", callback_data="admin_ab_tests")]
            ])
            
            await message.answer(
                f"✅ <b>A/B тест создан!</b>\n\n"
                f"🆔 ID: {ab_test.id}\n"
                f"📝 Название: {test_name}\n"
                f"👥 Охват: {population}% пользователей\n\n"
                f"<b>Вариант A:</b>\n<i>{preview_a}</i>\n\n"
                f"<b>Вариант B:</b>\n<i>{preview_b}</i>\n\n"
                f"📊 Метрика: Click-Through Rate (CTR)",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
    except Exception as e:
        logger.error(f"Error creating A/B test: {e}")
        await message.answer("❌ Ошибка при создании A/B теста. Попробуйте позже.")
    
    await state.clear()


@router.callback_query(F.data.startswith("ab_test_start:"))
@role_required(AdminRole.EDITOR)
async def ab_test_start(callback: CallbackQuery):
    """Start an A/B test."""
    ab_test_id = int(callback.data.split(":")[1])
    
    try:
        async with get_db() as session:
            from ..repositories.broadcast_repository import ABTestRepository
            
            ab_test_repo = ABTestRepository(session)
            success = await ab_test_repo.start_ab_test(ab_test_id)
            
            if success:
                await session.commit()
                await callback.message.edit_text(
                    f"✅ <b>A/B тест #{ab_test_id} запущен!</b>\n\n"
                    "Тест теперь активен и собирает данные.\n"
                    "Результаты можно посмотреть в разделе 'Результаты'.",
                    parse_mode="HTML"
                )
            else:
                await callback.answer("❌ Не удалось запустить тест", show_alert=True)
                
    except Exception as e:
        logger.error(f"Error starting A/B test: {e}")
        await callback.answer("❌ Ошибка при запуске теста", show_alert=True)


@router.callback_query(F.data == "ab_test_active")
@role_required(AdminRole.EDITOR)
async def ab_test_active_list(callback: CallbackQuery):
    """Show active A/B tests."""
    try:
        async with get_db() as session:
            from ..repositories.broadcast_repository import ABTestRepository
            
            ab_test_repo = ABTestRepository(session)
            active_tests = await ab_test_repo.get_running_ab_tests()
            
            if not active_tests:
                await callback.message.edit_text(
                    "📊 <b>Активные A/B тесты</b>\n\n"
                    "❌ Активных тестов нет\n\n"
                    "Создайте новый тест для начала работы.",
                    parse_mode="HTML"
                )
                return
            
            text = "📊 <b>Активные A/B тесты</b>\n\n"
            buttons = []
            
            for test in active_tests:
                days_running = (datetime.utcnow() - test.created_at).days
                text += f"🆔 <b>#{test.id}</b> - {test.name}\n"
                text += f"👥 {test.population}% пользователей\n"
                text += f"📅 Запущен {days_running} дней назад\n\n"
                
                buttons.append([InlineKeyboardButton(
                    text=f"📈 Результаты #{test.id}",
                    callback_data=f"ab_test_results:{test.id}"
                )])
                
                buttons.append([InlineKeyboardButton(
                    text=f"⏹️ Остановить #{test.id}",
                    callback_data=f"ab_test_stop:{test.id}"
                )])
            
            buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_ab_tests")])
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Error getting active A/B tests: {e}")
        await callback.answer("❌ Ошибка при загрузке тестов", show_alert=True)


@router.callback_query(F.data.startswith("ab_test_results:"))
@role_required(AdminRole.EDITOR)
async def ab_test_show_results(callback: CallbackQuery):
    """Show A/B test results."""
    ab_test_id = int(callback.data.split(":")[1])
    
    try:
        async with get_db() as session:
            from ..repositories.broadcast_repository import ABTestRepository
            
            ab_test_repo = ABTestRepository(session)
            analytics = await ab_test_repo.get_ab_test_analytics(ab_test_id)
            
            if not analytics:
                await callback.answer("❌ Тест не найден", show_alert=True)
                return
            
            text = f"📈 <b>Результаты A/B теста</b>\n\n"
            text += f"🆔 <b>#{analytics['test_id']}</b> - {analytics['test_name']}\n"
            text += f"📊 Метрика: {analytics['metric']}\n"
            text += f"📤 Всего отправлено: {analytics['total_delivered']}\n"
            text += f"👆 Всего кликов: {analytics['total_clicks']}\n"
            text += f"💰 Всего конверсий: {analytics['total_conversions']}\n\n"
            
            for variant in analytics['variants']:
                text += f"<b>Вариант {variant['variant_code']}:</b>\n"
                text += f"📤 Отправлено: {variant['delivered']}\n"
                text += f"👆 Клики: {variant['clicks']} (CTR: {variant['ctr']}%)\n"
                text += f"💰 Конверсии: {variant['conversions']} (CR: {variant['cr']}%)\n\n"
            
            if analytics['winner']:
                text += f"🏆 <b>Победитель:</b> Вариант {analytics['winner']}"
            else:
                text += "⏳ Недостаточно данных для определения победителя"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ К активным тестам", callback_data="ab_test_active")]
            ])
            
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Error getting A/B test results: {e}")
        await callback.answer("❌ Ошибка при загрузке результатов", show_alert=True)


@router.callback_query(F.data.startswith("ab_test_stop:"))
@role_required(AdminRole.EDITOR)
async def ab_test_stop(callback: CallbackQuery):
    """Stop an A/B test."""
    ab_test_id = int(callback.data.split(":")[1])
    
    try:
        async with get_db() as session:
            from ..repositories.broadcast_repository import ABTestRepository
            
            ab_test_repo = ABTestRepository(session)
            success = await ab_test_repo.stop_ab_test(ab_test_id)
            
            if success:
                await session.commit()
                
                # Get final results
                analytics = await ab_test_repo.get_ab_test_analytics(ab_test_id)
                winner = analytics.get('winner', 'Неопределен')
                
                await callback.message.edit_text(
                    f"⏹️ <b>A/B тест #{ab_test_id} остановлен</b>\n\n"
                    f"🏆 Победитель: <b>Вариант {winner}</b>\n\n"
                    "Тест завершен. Полные результаты доступны в разделе 'Результаты'.",
                    parse_mode="HTML"
                )
            else:
                await callback.answer("❌ Не удалось остановить тест", show_alert=True)
                
    except Exception as e:
        logger.error(f"Error stopping A/B test: {e}")
        await callback.answer("❌ Ошибка при остановке теста", show_alert=True)


# Materials Management
@router.callback_query(F.data == "admin_materials")
@role_required(AdminRole.EDITOR)
async def materials_management(callback: CallbackQuery):
   """Materials management menu."""
   keyboard = InlineKeyboardMarkup(inline_keyboard=[
       [InlineKeyboardButton(text="📦 Материалы из БД", callback_data="material_db_menu")],
       [InlineKeyboardButton(text="📄 Материалы из Excel", callback_data="material_excel_menu")],
       [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
   ])
   
   await callback.message.edit_text(
       "📚 <b>Управление материалами</b>\n\n"
       "Выберите источник материалов:",
       reply_markup=keyboard,
       parse_mode="HTML"
   )

@router.callback_query(F.data == "material_db_menu")
@role_required(AdminRole.EDITOR)
async def materials_db_management(callback: CallbackQuery):
   """Materials management menu for DB."""
   keyboard = InlineKeyboardMarkup(inline_keyboard=[
       [InlineKeyboardButton(text="🆕 Новый материал", callback_data="material_create")],
       [InlineKeyboardButton(text="📚 Все материалы", callback_data="material_list")],
       [InlineKeyboardButton(text="📊 Статистика", callback_data="material_stats")],
       [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_materials")]
   ])
   
   await callback.message.edit_text(
       "📦 <b>Материалы из Базы Данных</b>\n\n"
       "Выберите действие:",
       reply_markup=keyboard,
       parse_mode="HTML"
   )

@router.callback_query(F.data == "excel_upload")
@role_required(AdminRole.EDITOR)
async def request_excel_file(callback: CallbackQuery, state: FSMContext, **kwargs):
  """Prompts admin to upload the materials.xlsx file."""
  await state.set_state(AdminStates.waiting_for_excel_file)
  await callback.message.edit_text(
      "📥 <b>Загрузка Excel файла</b>\n\n"
      "Пожалуйста, отправьте файл `materials.xlsx`.\n\n"
      "Требования к файлу:\n"
      "• Лист с названием `materials`\n"
      "• Колонки: `title`, `text`, `media_filename`",
      parse_mode="HTML"
  )

@router.message(AdminStates.waiting_for_excel_file, F.document)
@role_required(AdminRole.EDITOR)
async def process_excel_file(message: Message, state: FSMContext, **kwargs):
  """Processes the uploaded materials.xlsx file."""
  if message.document.file_name != 'materials.xlsx':
      await message.answer("❌ Неверное имя файла. Пожалуйста, загрузите файл с именем `materials.xlsx`.")
      return

  try:
      from app.bot import bot
      file = await bot.get_file(message.document.file_id)
      await bot.download_file(file.file_path, excel_material_service.EXCEL_FILE_PATH)
      
      # Validate the file
      validation_result = excel_material_service.validate_excel_file()
      
      if not validation_result:
          await message.answer("❌ Не удалось прочитать или обработать файл. Проверьте его структуру.")
      else:
          summary = (
              f"✅ <b>Файл успешно загружен и провалидирован!</b>\n\n"
              f"📈 <b>Сводка:</b>\n"
              f"• Всего строк: {validation_result.total_rows}\n"
              f"• Готово к отправке: {validation_result.valid_rows}\n"
              f"• Пропущено: {validation_result.skipped_rows}\n\n"
          )
          if validation_result.reasons:
              summary += "<b>Причины пропуска:</b>\n"
              for reason, count in validation_result.reasons.items():
                  summary += f"• {reason}: {count}\n"
          
          await message.answer(summary, parse_mode="HTML")

  except Exception as e:
      logger.error(f"Error processing Excel file: {e}", exc_info=True)
      await message.answer("❌ Произошла ошибка при обработке файла.")
  
  await state.clear()
  # Show the updated menu
  await materials_excel_management(message, **kwargs)


@router.callback_query(F.data == "excel_media_upload")
@role_required(AdminRole.EDITOR)
async def request_media_files(callback: CallbackQuery, state: FSMContext, **kwargs):
  """Prompts admin to upload media files."""
  await state.set_state(AdminStates.waiting_for_media_files)
  await callback.message.edit_text(
      "🖼️ <b>Загрузка медиа файлов</b>\n\n"
      "Отправьте фото или видео. Можно отправить несколько файлов одним сообщением.\n\n"
      "Поддерживаемые форматы:\n"
      "• Фото: jpg, jpeg, png, webp\n"
      "• Видео: mp4, mov, webm",
      parse_mode="HTML"
  )

@router.message(AdminStates.waiting_for_media_files, F.photo | F.video)
@role_required(AdminRole.EDITOR)
async def process_media_files(message: Message, state: FSMContext, **kwargs):
  """Processes uploaded media files."""
  file_id = None
  file_name = None
  
  try:
      if message.photo:
          file_id = message.photo[-1].file_id
          file_name = f"{file_id}.jpg"
      elif message.video:
          file_id = message.video.file_id
          file_name = message.video.file_name or f"{file_id}.mp4"

      if file_id and file_name:
          from app.bot import bot
          file_info = await bot.get_file(file_id)
          destination_path = os.path.join(excel_material_service.MEDIA_PATH, file_name)
          await bot.download_file(file_info.file_path, destination_path)
          await message.answer(f"✅ Файл `{file_name}` успешно загружен.")
      else:
          await message.answer("❌ Не удалось определить файл.")

  except Exception as e:
      logger.error(f"Error processing media file: {e}", exc_info=True)
      await message.answer(f"❌ Ошибка при загрузке файла `{file_name}`.")

@router.callback_query(F.data == "excel_schedule_settings")
@role_required(AdminRole.EDITOR)
async def excel_schedule_settings(callback: CallbackQuery, **kwargs):
   """Displays schedule settings and controls."""
   config = excel_material_service.get_schedule_config()
   
   freq_map = {
       "daily_1": "1 раз в сутки", "daily_2": "2 раза в сутки",
       "every_2": "Раз в 2 дня", "every_3": "Раз в 3 дня",
       "every_4": "Раз в 4 дня", "every_5": "Раз в 5 дней",
       "every_6": "Раз в 6 дней", "weekly": "Раз в неделю"
   }
   current_freq = freq_map.get(config.get('frequency'), "Не задана")
   status = "⏸️ На паузе" if config.get('paused') else "▶️ Активна"

   text = (
       f"📋 <b>Настройки расписания рассылки</b>\n\n"
       f"Текущий статус: <b>{status}</b>\n"
       f"Частота: <b>{current_freq}</b>\n"
       f"Окно отправки: <b>{config.get('window_start_h_msk', 11)}:00 - {config.get('window_end_h_msk', 20)}:00 МСК</b>\n\n"
       "Выберите действие:"
   )

   pause_resume_button = InlineKeyboardButton(
       text="▶️ Возобновить" if config.get('paused') else "⏸️ Поставить на паузу",
       callback_data="excel_schedule_toggle_pause"
   )

   keyboard = InlineKeyboardMarkup(inline_keyboard=[
       [
           InlineKeyboardButton(text="1 раз/сутки", callback_data="excel_freq:daily_1"),
           InlineKeyboardButton(text="2 раза/сутки", callback_data="excel_freq:daily_2")
       ],
       [
           InlineKeyboardButton(text="Раз в 2 дня", callback_data="excel_freq:every_2"),
           InlineKeyboardButton(text="Раз в 3 дня", callback_data="excel_freq:every_3"),
       ],
       [
           InlineKeyboardButton(text="Раз в 4 дня", callback_data="excel_freq:every_4"),
           InlineKeyboardButton(text="Раз в 5 дней", callback_data="excel_freq:every_5"),
       ],
       [
           InlineKeyboardButton(text="Раз в 6 дней", callback_data="excel_freq:every_6"),
           InlineKeyboardButton(text="Раз в неделю", callback_data="excel_freq:weekly"),
       ],
       [pause_resume_button],
       [InlineKeyboardButton(text="⬅️ Назад", callback_data="material_excel_menu")]
   ])

   await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data.startswith("excel_freq:"))
@role_required(AdminRole.EDITOR)
async def set_excel_schedule_frequency(callback: CallbackQuery, **kwargs):
   """Sets the frequency for the excel material mailing."""
   freq = callback.data.split(":")[1]
   
   config = excel_material_service.get_schedule_config()
   config['frequency'] = freq
   excel_material_service.save_schedule_config(config)
   
   scheduler_service.reschedule_excel_materials_mailing()
   
   await callback.answer(f"✅ Частота обновлена!")
   await excel_schedule_settings(callback, **kwargs)


@router.callback_query(F.data == "excel_schedule_toggle_pause")
@role_required(AdminRole.EDITOR)
async def toggle_excel_schedule_pause(callback: CallbackQuery, **kwargs):
   """Pauses or resumes the excel material mailing."""
   config = excel_material_service.get_schedule_config()
   config['paused'] = not config.get('paused', False)
   excel_material_service.save_schedule_config(config)
   
   scheduler_service.reschedule_excel_materials_mailing()
   
   status = "приостановлена" if config['paused'] else "возобновлена"
   await callback.answer(f"✅ Рассылка {status}!")
   await excel_schedule_settings(callback, **kwargs)

@router.callback_query(F.data == "material_excel_menu")
@role_required(AdminRole.EDITOR)
async def excel_logs(callback: CallbackQuery, **kwargs):
   """Displays the last 20 log entries for excel material sends."""
   logs = excel_material_service.get_latest_log_entries(limit=20)

   if not logs:
       text = "📊 <b>Логи отправки материалов из Excel</b>\n\nЗаписи отсутствуют."
   else:
       text = "📊 <b>Последние 20 записей лога:</b>\n\n"
       for log in reversed(logs):
           ts = log.get('ts_utc', '').replace('T', ' ')[:19]
           status_icon = "✅" if log.get('status') == 'success' else "❌"
           text += (
               f"{status_icon} <b>{ts}</b> - Пользователю @{log.get('username', log.get('user_id'))}\n"
               f"   Строка: {log.get('row')}, Файл: {log.get('media_filename')}\n"
           )
           if log.get('status') != 'success':
               text += f"   Ошибка: {log.get('error', 'N/A')}\n"
   
   keyboard = InlineKeyboardMarkup(inline_keyboard=[
       [InlineKeyboardButton(text="⬅️ Назад", callback_data="material_excel_menu")]
   ])

   await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")

@router.callback_query(F.data == "excel_test_send")
@role_required(AdminRole.EDITOR)
async def excel_test_send_request(callback: CallbackQuery, state: FSMContext, **kwargs):
   """Requests a username for a test send."""
   await state.set_state(AdminStates.waiting_for_test_username)
   await callback.message.edit_text(
       "📲 <b>Тестовая отправка</b>\n\n"
       "Введите @username пользователя для отправки следующего материала.\n"
       "Убедитесь, что пользователь запустил бота.",
       parse_mode="HTML"
   )

@router.message(AdminStates.waiting_for_test_username, F.text)
@role_required(AdminRole.EDITOR)
async def excel_test_send_execute(message: Message, state: FSMContext, **kwargs):
   """Executes a test send to a specific user."""
   username = message.text.strip()
   if not username.startswith('@'):
       await message.answer("❌ Неверный формат. Имя пользователя должно начинаться с @.")
       return

   clean_username = username[1:]

   try:
       async for db in get_db():
           user_repo = AdminRepository(db) # Using AdminRepository to find user
           user = await user_repo.find_user_by_username(clean_username)
           break
       
       if not user:
           await message.answer(f"❌ Пользователь {username} не найден.")
           await state.clear()
           return

       material = excel_material_service.get_next_material_for_user(user.id)

       if not material:
           await message.answer("❌ Нет доступных материалов для отправки.")
           await state.clear()
           return
       
       from aiogram.types import FSInputFile
       from app.bot import bot

       caption = material.text
       
       if material.media_type == 'photo':
           await bot.send_photo(
               chat_id=user.telegram_id,
               photo=FSInputFile(material.media_path),
               caption=caption
           )
       elif material.media_type == 'video':
           await bot.send_video(
               chat_id=user.telegram_id,
               video=FSInputFile(material.media_path),
               caption=caption
           )
       
       # Update progress for the test user
       excel_material_service.update_user_progress(user.id, material.row_index)
       excel_material_service.log_send_attempt(
           user_id=user.id,
           username=user.username,
           material=material,
           status='success',
           error='test_send'
       )
       
       await message.answer(
           f"✅ Материал `{material.media_filename}` (строка {material.row_index}) "
           f"успешно отправлен пользователю {username}."
       )

   except Exception as e:
       logger.error(f"Error during test send to {username}: {e}", exc_info=True)
       await message.answer(f"❌ Произошла ошибка при отправке материала пользователю {username}.")
   
   await state.clear()


@router.callback_query(F.data == "material_excel_menu")
@role_required(AdminRole.EDITOR)
async def materials_excel_management(callback: CallbackQuery, **kwargs):
   """Excel materials management menu."""
  keyboard = InlineKeyboardMarkup(inline_keyboard=[
      [InlineKeyboardButton(text="📥 Загрузить Excel", callback_data="excel_upload")],
      [InlineKeyboardButton(text="🖼️ Загрузить медиа", callback_data="excel_media_upload")],
      [InlineKeyboardButton(text="📋 Настройки расписания", callback_data="excel_schedule_settings")],
      [InlineKeyboardButton(text="📲 Тестовая отправка", callback_data="excel_test_send")],
      [InlineKeyboardButton(text="📊 Логи отправки", callback_data="excel_logs")],
      [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_materials")]
  ])
  
  # Get current status
  validation_result = excel_material_service.validate_excel_file()
  status_text = "⚠️ Файл `materials.xlsx` не найден или поврежден."
  if validation_result:
      status_text = (
          f"✅ Файл `materials.xlsx` загружен.\n"
          f"Всего строк: {validation_result.total_rows}\n"
          f"Готово к отправке: {validation_result.valid_rows}\n"
          f"Пропущено: {validation_result.skipped_rows}"
      )

  await callback.message.edit_text(
      f"📄 <b>Материалы из Excel</b>\n\n"
      f"<b>Статус:</b>\n{status_text}\n\n"
      "Выберите действие:",
      reply_markup=keyboard,
      parse_mode="HTML"
  )


@router.callback_query(F.data == "material_create")
@role_required(AdminRole.EDITOR)
async def material_create_step1(callback: CallbackQuery, state: FSMContext):
    """Create material - step 1: title."""
    await state.set_state(AdminStates.waiting_for_material_title)
    await callback.message.edit_text(
        "📚 <b>Новый материал</b>\n\n"
        "Шаг 1/4: Введите заголовок материала:",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_material_title)
@role_required(AdminRole.EDITOR)
async def material_create_step2(message: Message, state: FSMContext):
    """Create material - step 2: content."""
    title = message.text.strip()
    
    if len(title) < 3:
        await message.answer("❌ Заголовок должен содержать минимум 3 символа. Попробуйте снова:")
        return
    
    await state.update_data(title=title)
    await state.set_state(AdminStates.waiting_for_material_content)
    
    await message.answer(
        f"✅ Заголовок: <b>{title}</b>\n\n"
        "Шаг 2/4: Введите содержание материала:\n\n"
        "💡 <i>Можете вставить текст, HTML разметку или краткое описание.</i>",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_material_content)
@role_required(AdminRole.EDITOR)
async def material_create_step3(message: Message, state: FSMContext):
    """Create material - step 3: URL (optional)."""
    content = message.text.strip()
    
    if len(content) < 10:
        await message.answer("❌ Содержание должно содержать минимум 10 символов. Попробуйте снова:")
        return
    
    await state.update_data(content=content)
    await state.set_state(AdminStates.waiting_for_material_url)
    
    content_preview = content[:150] + "..." if len(content) > 150 else content
    
    await message.answer(
        f"✅ Содержание добавлено: <i>{content_preview}</i>\n\n"
        "Шаг 3/4: Введите URL материала (ссылка на видео, статью и т.д.):\n\n"
        "💡 <i>Если URL не нужен, напишите 'нет' или 'skip'</i>",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_material_url)
@role_required(AdminRole.EDITOR)
async def material_create_step4(message: Message, state: FSMContext):
    """Create material - step 4: tags."""
    url_text = message.text.strip().lower()
    
    # Check if URL should be skipped
    material_url = None
    if url_text not in ['нет', 'skip', 'no', '-']:
        material_url = message.text.strip()
        
        # Basic URL validation
        if material_url and not (material_url.startswith('http://') or material_url.startswith('https://')):
            await message.answer("❌ URL должен начинаться с http:// или https://. Попробуйте снова или введите 'нет':")
            return
    
    await state.update_data(url=material_url)
    await state.set_state(AdminStates.waiting_for_material_tags)
    
    url_display = material_url if material_url else "Не указан"
    
    await message.answer(
        f"✅ URL: <b>{url_display}</b>\n\n"
        "Шаг 4/4: Введите теги через запятую:\n\n"
        "💡 <i>Например: новичкам, трейдинг, биткоин, безопасность</i>\n\n"
        "Теги помогают системе показывать материал нужным пользователям.",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_material_tags)
@role_required(AdminRole.EDITOR)
async def material_create_final(message: Message, state: FSMContext):
    """Create material - final step: save."""
    tags_text = message.text.strip()
    
    if len(tags_text) < 3:
        await message.answer("❌ Введите хотя бы один тег (минимум 3 символа). Попробуйте снова:")
        return
    
    # Parse tags
    tags = [tag.strip() for tag in tags_text.split(',') if tag.strip()]
    
    if len(tags) == 0:
        await message.answer("❌ Введите хотя бы один корректный тег. Попробуйте снова:")
        return
    
    # Get all data from state
    data = await state.get_data()
    title = data['title']
    content = data['content']
    url = data.get('url')
    
    try:
        async with get_db() as session:
            from ..repositories.material_repository import MaterialRepository
            from ..models import MaterialType
            
            material_repo = MaterialRepository(session)
            
            # Determine material type based on content
            material_type = MaterialType.ARTICLE  # Default
            if url:
                if 'youtube.com' in url or 'youtu.be' in url:
                    material_type = MaterialType.ARTICLE  # Could be VIDEO if you have this type
            
            # Create material
            material = await material_repo.create_material(
                type=material_type,
                title=title,
                body=content,
                url=url,
                tags=tags,
                segments=["COLD", "WARM", "HOT"]  # Available to all segments by default
            )
            
            await session.commit()
            
            content_preview = content[:200] + "..." if len(content) > 200 else content
            url_display = f"\n🔗 URL: {url}" if url else ""
            tags_display = ", ".join(tags)
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📚 Все материалы", callback_data="material_list")],
                [InlineKeyboardButton(text="🆕 Создать ещё", callback_data="material_create")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_materials")]
            ])
            
            await message.answer(
                f"✅ <b>Материал создан!</b>\n\n"
                f"🆔 ID: {material.id}\n"
                f"📝 Заголовок: {title}\n"
                f"📄 Содержание: <i>{content_preview}</i>{url_display}\n"
                f"🏷️ Теги: {tags_display}\n"
                f"🎯 Доступен для: Всех сегментов",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
    except Exception as e:
        logger.error(f"Error creating material: {e}")
        await message.answer("❌ Ошибка при создании материала. Попробуйте позже.")
    
    await state.clear()


@router.callback_query(F.data == "material_list")
@role_required(AdminRole.EDITOR)
async def material_list_show(callback: CallbackQuery):
    """Show all materials."""
    try:
        async with get_db() as session:
            from ..repositories.material_repository import MaterialRepository
            
            material_repo = MaterialRepository(session)
            materials = await material_repo.get_recent_materials(limit=10)
            
            if not materials:
                await callback.message.edit_text(
                    "📚 <b>Материалы</b>\n\n"
                    "❌ Материалов пока нет\n\n"
                    "Создайте первый материал для начала работы.",
                    parse_mode="HTML"
                )
                return
            
            text = "📚 <b>Последние материалы</b>\n\n"
            buttons = []
            
            for material in materials:
                created_date = material.created_at.strftime('%d.%m.%Y')
                tags_preview = ", ".join(material.tags[:3]) if material.tags else "Без тегов"
                if len(material.tags) > 3:
                    tags_preview += "..."
                
                text += f"🆔 <b>#{material.id}</b> - {material.title}\n"
                text += f"📅 {created_date}\n"
                text += f"🏷️ {tags_preview}\n"
                text += f"📊 Статус: {'✅ Активен' if material.is_active else '❌ Отключен'}\n\n"
                
                buttons.append([InlineKeyboardButton(
                    text=f"✏️ Редактировать #{material.id}",
                    callback_data=f"material_edit:{material.id}"
                )])
            
            buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_materials")])
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Error getting materials list: {e}")
        await callback.answer("❌ Ошибка при загрузке материалов", show_alert=True)


@router.callback_query(F.data == "material_stats")
@role_required(AdminRole.EDITOR)
async def material_stats_show(callback: CallbackQuery):
    """Show material statistics."""
    try:
        async with get_db() as session:
            from ..repositories.material_repository import MaterialRepository
            from sqlalchemy import select, func
            from ..models import Material, MaterialType
            
            # Get total counts
            total_result = await session.execute(select(func.count(Material.id)))
            total_materials = total_result.scalar()
            
            active_result = await session.execute(
                select(func.count(Material.id)).where(Material.is_active == True)
            )
            active_materials = active_result.scalar()
            
            # Get counts by type
            type_stats = {}
            for material_type in MaterialType:
                type_result = await session.execute(
                    select(func.count(Material.id)).where(
                        Material.type == material_type
                    )
                )
                count = type_result.scalar()
                if count > 0:
                    type_stats[material_type.value] = count
            
            text = "📊 <b>Статистика материалов</b>\n\n"
            text += f"📚 Всего материалов: {total_materials}\n"
            text += f"✅ Активных: {active_materials}\n"
            text += f"❌ Неактивных: {total_materials - active_materials}\n\n"
            
            if type_stats:
                text += "<b>По типам:</b>\n"
                for material_type, count in type_stats.items():
                    text += f"• {material_type}: {count}\n"
            else:
                text += "📝 Материалов по типам пока нет"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_materials")]
            ])
            
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Error getting material stats: {e}")
        await callback.answer("❌ Ошибка при загрузке статистики", show_alert=True)


@router.callback_query(F.data == "admin_products")
@role_required(AdminRole.EDITOR)
async def products_management(callback: CallbackQuery):
    """Products management menu."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Новый продукт", callback_data="product_create")],
        [InlineKeyboardButton(text="💰 Все продукты", callback_data="product_list")],
        [InlineKeyboardButton(text="📊 Статистика продаж", callback_data="product_stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(
        "💰 <b>Управление продуктами</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


# Leads Management
@router.callback_query(F.data == "admin_leads")
@admin_required
async def leads_management(callback: CallbackQuery):
    """Leads management menu."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Новые лиды", callback_data="leads_new")],
        [InlineKeyboardButton(text="🔄 В работе", callback_data="leads_in_progress")],
        [InlineKeyboardButton(text="✅ Завершённые", callback_data="leads_completed")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(
        "👥 <b>Управление лидами</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admin_back")
@admin_required  
async def admin_back(callback: CallbackQuery, state: FSMContext):
    """Go back to admin panel."""
    await state.clear()
    await admin_panel(callback)


def register_handlers(dp: Dispatcher) -> None:
    """Register enhanced admin handlers."""
    dp.include_router(router)


def register_enhanced_admin_handlers(dp):
    """Register enhanced admin handlers."""
    dp.include_router(router)