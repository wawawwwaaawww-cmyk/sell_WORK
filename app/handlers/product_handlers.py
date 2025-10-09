"""Product management handlers for admin panel."""

import logging
from datetime import datetime
from typing import Optional
import uuid
from decimal import Decimal

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from ..db import get_db
from ..models import Product, Payment, PaymentStatus, AdminRole
from .admin_full import role_required, AdminStates

logger = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data == "product_create")
@role_required(AdminRole.EDITOR)
async def product_create_step1(callback: CallbackQuery, state: FSMContext):
    """Create product - step 1: name."""
    await state.set_state(AdminStates.waiting_for_product_name)
    await callback.message.edit_text(
        "💰 <b>Новый продукт</b>\n\n"
        "Шаг 1/3: Введите название продукта:\n\n"
        "💡 <i>Например: Курс 'DeFi для начинающих'</i>",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_product_name)
@role_required(AdminRole.EDITOR)
async def product_create_step2(message: Message, state: FSMContext):
    """Create product - step 2: price."""
    product_name = message.text.strip()
    
    if len(product_name) < 5:
        await message.answer("❌ Название должно содержать минимум 5 символов. Попробуйте снова:")
        return
    
    await state.update_data(product_name=product_name)
    await state.set_state(AdminStates.waiting_for_product_price)
    
    await message.answer(
        f"✅ Название: <b>{product_name}</b>\n\n"
        "Шаг 2/3: Введите цену продукта (в долларах):\n\n"
        "💡 <i>Например: 199 или 199.99</i>\n\n"
        "📊 <b>Рекомендуемые цены по сегментам:</b>\n"
        "• Новички (COLD): $50-200\n"
        "• Трейдеры (WARM): $200-500\n"
        "• Инвесторы (HOT): $500+",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_product_price)
@role_required(AdminRole.EDITOR)
async def product_create_step3(message: Message, state: FSMContext):
    """Create product - step 3: description."""
    try:
        price = float(message.text.strip())
        if price <= 0:
            raise ValueError("Price must be positive")
    except ValueError:
        await message.answer("❌ Введите корректную цену (положительное число). Попробуйте снова:")
        return
    
    await state.update_data(price=price)
    await state.set_state(AdminStates.waiting_for_product_description)
    
    # Determine segment recommendation based on price
    segment_recommendation = ""
    if price <= 200:
        segment_recommendation = "🔵 Рекомендуется для: Новички (COLD)"
    elif price <= 500:
        segment_recommendation = "🟡 Рекомендуется для: Трейдеры (WARM)"
    else:
        segment_recommendation = "🔴 Рекомендуется для: Инвесторы (HOT)"
    
    await message.answer(
        f"✅ Цена: <b>${price}</b>\n"
        f"{segment_recommendation}\n\n"
        "Шаг 3/3: Введите описание продукта:\n\n"
        "💡 <i>Опишите, что включает курс, какая польза и что получит клиент</i>",
        parse_mode="HTML"
    )


@router.message(AdminStates.waiting_for_product_description)
@role_required(AdminRole.EDITOR)
async def product_create_final(message: Message, state: FSMContext):
    """Create product - final step: save."""
    description = message.text.strip()
    
    if len(description) < 20:
        await message.answer("❌ Описание должно содержать минимум 20 символов. Попробуйте снова:")
        return
    
    # Get all data from state
    data = await state.get_data()
    product_name = data['product_name']
    price = data['price']
    
    try:
        async with get_db() as session:
            # Generate unique product code
            product_code = f"COURSE_{uuid.uuid4().hex[:8].upper()}"
            
            # Create product metadata based on price/segment
            meta = {
                "target_segments": [],
                "difficulty": "intermediate",
                "duration_weeks": 4,
                "includes": []
            }
            
            # Set target segments based on price
            if price <= 200:
                meta["target_segments"] = ["COLD"]
                meta["difficulty"] = "beginner"
            elif price <= 500:
                meta["target_segments"] = ["WARM"]
                meta["difficulty"] = "intermediate"
            else:
                meta["target_segments"] = ["HOT"]
                meta["difficulty"] = "advanced"
            
            # Create product
            product = Product(
                code=product_code,
                name=product_name,
                description=description,
                price=Decimal(str(price)),
                meta=meta,
                is_active=True
            )
            
            session.add(product)
            await session.flush()
            await session.refresh(product)
            await session.commit()
            
            # Determine display segments
            segments_display = ", ".join(meta["target_segments"])
            description_preview = description[:200] + "..." if len(description) > 200 else description
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💰 Все продукты", callback_data="product_list")],
                [InlineKeyboardButton(text="🆕 Создать ещё", callback_data="product_create")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_products")]
            ])
            
            await message.answer(
                f"✅ <b>Продукт создан!</b>\n\n"
                f"🆔 ID: {product.id}\n"
                f"📝 Название: {product_name}\n"
                f"💰 Цена: ${price}\n"
                f"🔑 Код: {product_code}\n"
                f"🎯 Цель: {segments_display}\n\n"
                f"📄 Описание: <i>{description_preview}</i>\n\n"
                f"⚙️ Уровень: {meta['difficulty']}\n"
                f"📅 Продолжительность: {meta['duration_weeks']} недель",
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
    except Exception as e:
        logger.error(f"Error creating product: {e}")
        await message.answer("❌ Ошибка при создании продукта. Попробуйте позже.")
    
    await state.clear()


@router.callback_query(F.data == "product_list")
@role_required(AdminRole.EDITOR)
async def product_list_show(callback: CallbackQuery):
    """Show all products."""
    try:
        async with get_db() as session:
            # Get all products
            stmt = select(Product).order_by(Product.id.desc()).limit(10)
            result = await session.execute(stmt)
            products = result.scalars().all()
            
            if not products:
                await callback.message.edit_text(
                    "💰 <b>Продукты</b>\n\n"
                    "❌ Продуктов пока нет\n\n"
                    "Создайте первый продукт для начала работы.",
                    parse_mode="HTML"
                )
                return
            
            text = "💰 <b>Список продуктов</b>\n\n"
            buttons = []
            
            for product in products:
                target_segments = product.meta.get("target_segments", []) if product.meta else []
                segments_display = ", ".join(target_segments) if target_segments else "Все"
                
                text += f"🆔 <b>#{product.id}</b> - {product.name}\n"
                text += f"💰 Цена: ${product.price}\n"
                text += f"🎯 Сегменты: {segments_display}\n"
                text += f"📊 Статус: {'✅ Активен' if product.is_active else '❌ Отключен'}\n\n"
                
                buttons.append([InlineKeyboardButton(
                    text=f"✏️ Редактировать #{product.id}",
                    callback_data=f"product_edit:{product.id}"
                )])
            
            buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_products")])
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Error getting products list: {e}")
        await callback.answer("❌ Ошибка при загрузке продуктов", show_alert=True)


@router.callback_query(F.data == "product_stats")
@role_required(AdminRole.EDITOR)
async def product_stats_show(callback: CallbackQuery):
    """Show product sales statistics."""
    try:
        async with get_db() as session:
            # Get total products
            total_products_result = await session.execute(select(func.count(Product.id)))
            total_products = total_products_result.scalar()
            
            active_products_result = await session.execute(
                select(func.count(Product.id)).where(Product.is_active == True)
            )
            active_products = active_products_result.scalar()
            
            # Get sales statistics
            total_sales_result = await session.execute(
                select(func.count(Payment.id)).where(Payment.status == PaymentStatus.PAID)
            )
            total_sales = total_sales_result.scalar()
            
            total_revenue_result = await session.execute(
                select(func.sum(Payment.amount)).where(Payment.status == PaymentStatus.PAID)
            )
            total_revenue = total_revenue_result.scalar() or 0
            
            # Get top selling products
            top_products_stmt = select(
                Product.name,
                func.count(Payment.id).label('sales_count'),
                func.sum(Payment.amount).label('revenue')
            ).join(
                Payment, Product.id == Payment.product_id
            ).where(
                Payment.status == PaymentStatus.PAID
            ).group_by(
                Product.id, Product.name
            ).order_by(
                func.count(Payment.id).desc()
            ).limit(5)
            
            top_products_result = await session.execute(top_products_stmt)
            top_products = top_products_result.all()
            
            text = "📊 <b>Статистика продаж</b>\n\n"
            text += f"💰 Всего продуктов: {total_products}\n"
            text += f"✅ Активных: {active_products}\n"
            text += f"❌ Неактивных: {total_products - active_products}\n\n"
            
            text += f"💵 Всего продаж: {total_sales}\n"
            text += f"💰 Общая выручка: ${total_revenue:.2f}\n\n"
            
            if top_products:
                text += "<b>🏆 Топ-5 по продажам:</b>\n"
                for i, (name, sales_count, revenue) in enumerate(top_products, 1):
                    text += f"{i}. {name}: {sales_count} продаж (${revenue:.2f})\n"
            else:
                text += "📝 Продаж пока нет"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_products")]
            ])
            
            await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Error getting product stats: {e}")
        await callback.answer("❌ Ошибка при загрузке статистики", show_alert=True)


def register_product_handlers(dp):
    """Register product handlers."""
    dp.include_router(router)