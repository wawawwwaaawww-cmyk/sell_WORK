"""Payment processing handlers."""

from decimal import Decimal
from typing import Dict, Any

import structlog
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import User, FunnelStage
from app.services.user_service import UserService
from app.services.payment_service import PaymentService
from app.services.event_service import EventService
from app.services.manager_notification_service import ManagerNotificationService
from app.services.lead_service import LeadService


router = Router()
logger = structlog.get_logger()


@router.callback_query(F.data.startswith("offer:pay:"))
async def handle_payment_offer(callback: CallbackQuery, user: User, user_service: UserService, **kwargs):
    """Handle payment offer request."""
    try:
        product_code = callback.data.split(":")[-1]
        
        payment_service = PaymentService(kwargs.get("session"))
        
        # Get product by code or use default
        if product_code == "advanced":
            # For hot segment users
            products = await payment_service.get_suitable_products(user)
            if not products:
                await callback.answer("Продукты временно недоступны")
                return
            product = products[0]  # Get first suitable product
        else:
            # Get all suitable products and let user choose
            products = await payment_service.get_suitable_products(user)
            if not products:
                await callback.answer("Продукты временно недоступны")
                return
            product = products[0]
        
        # Check payment eligibility
        eligible, message = await payment_service.check_payment_eligibility(user, product, payment_type="full")
        if not eligible:
            await callback.message.edit_text(
                f"❌ **Оплата недоступна**\n\n{message}\n\nОбратитесь к менеджеру для уточнения деталей.",
                parse_mode="Markdown"
            )
            await callback.answer("Оплата недоступна")
            return
        
        # Check for discounts/bonuses filter
        filter_text = f"""💳 **Готов приобрести программу \"{product.name}\"?**

💰 **Стоимость:** {product.price:,.0f} рублей

❓ **Важный вопрос перед оплатой:**

Есть ли у тебя промокод или бонусные рубли от предыдущих покупок?

• **Если да** — тебя подключит менеджер для оформления со скидкой
• **Если нет** — переходим к автоматической оплате

Выбери подходящий вариант:"""
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="🎫 Да, есть промокод/бонусы",
            callback_data=f"payment:discount:{product.id}"
        ))
        keyboard.add(InlineKeyboardButton(
            text="💳 Нет, оплачиваю полную стоимость",
            callback_data=f"payment:full:{product.id}"
        ))
        keyboard.add(InlineKeyboardButton(
            text="🔙 Назад к выбору программы",
            callback_data="llm:discuss_programs"
        ))
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            filter_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        await callback.answer()
        
    except Exception as e:
        logger.error("Error handling payment offer", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка при обработке запроса")


@router.callback_query(F.data.startswith("payment:discount:"))
async def handle_discount_payment(callback: CallbackQuery, user: User, **kwargs):
    """Handle payment with discount/bonus."""
    try:
        product_id = int(callback.data.split(":")[-1])
        
        # Create lead for manager handling
        lead_service = LeadService(kwargs.get("session"))
        context = {"payment_initiated": True, "discount_requested": True}
        
        if await lead_service.should_create_lead(user, context):
            lead = await lead_service.create_lead_from_user(
                user=user,
                trigger_event="payment_with_discount",
                conversation_summary=f"Запросил оплату программы со скидкой/бонусами. Product ID: {product_id}"
            )
            
            # Notify managers
            manager_service = ManagerNotificationService(callback.bot, kwargs.get("session"))
            await manager_service.notify_new_lead(lead, user)
        
        payment_service = PaymentService(kwargs.get("session"))
        await payment_service.create_payment_link(
            user_id=user.id,
            product_id=product_id,
            payment_type="manual",
            manual_link=True,
            conditions_note="Запрос менеджеру о скидке/рассрочке",
            discount_type="manual",
        )

        discount_text = f"""🎫 **Отлично! Менеджер обработает твою заявку**

✅ Твой запрос на оплату со скидкой передан менеджеру
⏰ Мы свяжемся с тобой в течение 10 минут
💬 Менеджер отправит персональную ссылку с учетом скидки

📱 Жди сообщение в этом чате!

💡 *Менеджер поможет применить все доступные бонусы и промокоды для максимальной экономии*

Спасибо за выбор нашей школы! 🚀"""
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="📚 Изучить материалы пока жду",
            callback_data="materials:educational"
        ))
        keyboard.add(InlineKeyboardButton(
            text="💬 Задать вопрос боту",
            callback_data="llm:ask_questions"
        ))
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            discount_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        # Log event
        event_service = EventService(kwargs.get("session"))
        await event_service.log_event(
            user_id=user.id,
            event_type="payment_discount_requested",
            payload={"product_id": product_id}
        )
        
        await callback.answer("✅ Менеджер скоро свяжется!")
        
    except Exception as e:
        logger.error("Error handling discount payment", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка")


@router.callback_query(F.data.startswith("payment:full:"))
async def handle_full_payment(callback: CallbackQuery, user: User, user_service: UserService, **kwargs):
    """Handle full payment without discounts."""
    try:
        product_id = int(callback.data.split(":")[-1])
        
        payment_service = PaymentService(kwargs.get("session"))
        
        # Get product
        product = await payment_service.product_repo.get_by_id(product_id)
        if not product:
            await callback.answer("Продукт не найден")
            return
        
        # Show payment options
        payment_options_text = f"""💳 **Оплата программы \"{product.name}\"**

💰 **Стоимость:** {product.price:,.0f} рублей

💡 **Выберите удобный способ оплаты:**

🔸 **Полная оплата** — единовременный платеж со скидкой
🔸 **Рассрочка** — разделите платеж на удобные части

Выберите подходящий вариант:"""
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="💳 Полная оплата",
            callback_data=f"payment:process:full:{product_id}"
        ))
        keyboard.add(InlineKeyboardButton(
            text="📅 Рассрочка",
            callback_data=f"payment:process:installment:{product_id}"
        ))
        keyboard.add(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data=f"offer:pay:{product.code}"
        ))
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            payment_options_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        await callback.answer()
        
    except Exception as e:
        logger.error("Error handling full payment", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка")


@router.callback_query(F.data.startswith("payment:process:"))
async def process_payment(callback: CallbackQuery, user: User, user_service: UserService, **kwargs):
    """Process payment and generate payment link."""
    try:
        parts = callback.data.split(":")
        payment_type = parts[2]  # full or installment
        product_id = int(parts[3])
        
        payment_service = PaymentService(kwargs.get("session"))
        
        # Get product
        product = await payment_service.product_repo.get_by_id(product_id)
        if not product:
            await callback.answer("Продукт не найден")
            return
        
        # Create payment link
        custom_amount = None
        if payment_type == "installment":
            # For installment, could be first payment amount
            custom_amount = product.price / 3  # Example: 3 installments
        
        success, payment_link, message = await payment_service.create_payment_link(
            user_id=user.id,
            product_id=product_id,
            payment_type=payment_type,
            custom_amount=custom_amount,
        )
        
        if success and payment_link:
            await user_service.advance_funnel_stage(user, FunnelStage.PAYMENT)
            
            offer_text = payment_service.get_payment_offer_text(
                user,
                product,
                payment_link,
                payment_type=payment_type,
                custom_amount=custom_amount,
            )
            
            if payment_type == "installment" and custom_amount is not None:
                offer_text += f"\n\n📅 **Рассрочка:** Первый платеж {custom_amount:,.0f} руб., далее по {custom_amount:,.0f} руб. ежемесячно"
            
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(
                text="💳 Перейти к оплате",
                url=payment_link
            ))
            keyboard.add(InlineKeyboardButton(
                text="❓ Вопросы по оплате",
                callback_data="manager:request"
            ))
            keyboard.adjust(1)
            
            await callback.message.edit_text(
                offer_text,
                reply_markup=keyboard.as_markup(),
                parse_mode="Markdown"
            )
            
            # Create lead for payment tracking
            lead_service = LeadService(kwargs.get("session"))
            context = {"payment_initiated": True}
            
            if await lead_service.should_create_lead(user, context):
                lead = await lead_service.create_lead_from_user(
                    user=user,
                    trigger_event="payment_initiated",
                    conversation_summary=f"Инициировал оплату программы {product.name}. Тип: {payment_type}"
                )
                
                # Notify managers
                manager_service = ManagerNotificationService(callback.bot, kwargs.get("session"))
                await manager_service.notify_payment_initiated(user, product.name, float(custom_amount or product.price))
            
            # Log event
            event_service = EventService(kwargs.get("session"))
            await event_service.log_payment_initiated(
                user_id=user.id,
                product_id=product_id,
                amount=float(custom_amount or product.price)
            )
            
            await callback.answer("💳 Ссылка на оплату готова!")
            
        elif success:
            await callback.message.edit_text(
                f"ℹ️ {message}\n\nМенеджер свяжется для завершения оплаты.",
                parse_mode="Markdown"
            )
            await callback.answer("Передал менеджеру")
        else:
            await callback.message.edit_text(
                f"❌ **Ошибка при создании ссылки на оплату**\n\n{message}\n\nПожалуйста, обратитесь к менеджеру.",
                parse_mode="Markdown"
            )
            await callback.answer("Ошибка создания ссылки")
            
    except Exception as e:
        logger.error("Error processing payment", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка при обработке платежа")


def register_handlers(dp):
    """Register payment handlers."""
    dp.include_router(router)
