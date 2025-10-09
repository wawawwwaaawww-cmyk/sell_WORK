"""Consultation scheduling handlers."""

from datetime import date, time
from typing import Dict, Any

import structlog
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from app.models import User, FunnelStage
from app.services.user_service import UserService
from app.services.consultation_service import ConsultationService
from app.services.event_service import EventService
from app.utils.callbacks import Callbacks
from app.handlers.scene_dispatcher import try_process_callback


class ConsultationStates(StatesGroup):
    waiting_custom_date = State()
    waiting_custom_time = State()


router = Router()
logger = structlog.get_logger()


@router.callback_query(F.data == "consult:offer")
async def offer_consultation(callback: CallbackQuery, user: User, **kwargs):
    """Offer consultation to user."""
    try:
        session = kwargs.get("session")
        if await try_process_callback(callback, session=session, user=user):
            return
        consultation_service = ConsultationService(session)
        
        # Check if user already has upcoming appointment
        upcoming = await consultation_service.repository.get_upcoming_appointments(user.id)
        if upcoming:
            appointment = upcoming[0]
            details = consultation_service.format_appointment_details(appointment)
            
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(
                text="📅 Перенести консультацию",
                callback_data="consult:reschedule"
            ))
            keyboard.add(InlineKeyboardButton(
                text="❌ Отменить консультацию",
                callback_data="consult:cancel"
            ))
            keyboard.adjust(1)
            
            await callback.message.edit_text(
                f"✅ **У тебя уже запланирована консультация!**\n\n{details}",
                reply_markup=keyboard.as_markup(),
                parse_mode="Markdown"
            )
            await callback.answer("У вас уже есть консультация")
            return
        
        # Get next 2 available dates
        available_dates = consultation_service.get_next_available_dates(days_ahead=5)
        
        offer_text = f"""📞 **Отлично, {user.first_name or 'друг'}! Запишем тебя на консультацию**

👨‍💼 **Что даст консультация:**
✅ Персональный анализ твоих целей
✅ Подбор оптимальной программы обучения  
✅ Ответы на все вопросы о криптовалютах
✅ Конкретный план действий

⏱ **Формат:** 15 минут в Telegram
💰 **Стоимость:** Бесплатно

📅 **Выбери удобную дату:**"""
        
        keyboard = InlineKeyboardBuilder()
        
        # Add first 2 dates
        for i, date_option in enumerate(available_dates[:2]):
            formatted_date = date_option.strftime("%d.%m (%a)")
            keyboard.add(InlineKeyboardButton(
                text=f"📅 {formatted_date}",
                callback_data=f"consult:date:{date_option.isoformat()}"
            ))
        
        keyboard.add(InlineKeyboardButton(
            text="📝 Другая дата",
            callback_data="consult:custom_date"
        ))
        keyboard.add(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back:main_menu"
        ))
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            offer_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        # Log event
        event_service = EventService(kwargs.get("session"))
        await event_service.log_event(
            user_id=user.id,
            event_type="consultation_offered",
            payload={}
        )
        
        await callback.answer("📞 Консультация!")
        
    except Exception as e:
        logger.error("Error offering consultation", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка")


@router.callback_query(F.data.startswith("consult:date:"))
async def select_consultation_date(callback: CallbackQuery, user: User, **kwargs):
    """Handle consultation date selection."""
    try:
        session = kwargs.get("session")
        date_str = callback.data.split(":")[-1]
        selected_date = date.fromisoformat(date_str)
        
        consultation_service = ConsultationService(session)
        
        # Get available slots for selected date
        available_slots = await consultation_service.get_available_slots_for_date(selected_date)
        
        if not available_slots:
            await callback.answer("К сожалению, на эту дату нет свободных слотов")
            return
        
        formatted_date = selected_date.strftime("%d.%m.%Y (%A)")
        
        time_text = f"""📅 **Дата выбрана: {formatted_date}**

⏰ **Выбери удобное время:**

{consultation_service.get_time_slots_text()}

Все время указано по Москве 🇷🇺"""
        
        keyboard = InlineKeyboardBuilder()
        
        for slot in available_slots:
            formatted_time = consultation_service.format_slot_time(slot)
            keyboard.add(InlineKeyboardButton(
                text=f"⏰ {formatted_time}",
                callback_data=f"consult:time:{date_str}:{slot.isoformat()}"
            ))
        
        keyboard.add(InlineKeyboardButton(
            text="🔙 Выбрать другую дату",
            callback_data="consult:offer"
        ))
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            time_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        await callback.answer()
        
    except Exception as e:
        logger.error("Error selecting consultation date", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка при выборе даты")


@router.callback_query(F.data.startswith("consult:time:"))
async def select_consultation_time(callback: CallbackQuery, user: User, user_service: UserService, **kwargs):
    """Handle consultation time selection."""
    try:
        parts = callback.data.split(":")
        date_str = parts[2]
        time_str = parts[3]
        
        session = kwargs.get("session")
        selected_date = date.fromisoformat(date_str)
        selected_time = time.fromisoformat(time_str)
        
        consultation_service = ConsultationService(session)
        
        # Book consultation
        success, appointment, message = await consultation_service.book_consultation(
            user_id=user.id,
            consultation_date=selected_date,
            slot=selected_time
        )
        
        if success and appointment:
            # Update user funnel stage
            await user_service.advance_funnel_stage(user, FunnelStage.CONSULTATION)
            
            # Format confirmation message
            details = consultation_service.format_appointment_details(appointment)
            
            confirmation_text = f"""✅ **Консультация успешно запланирована!**

{details}

🎉 **Что дальше:**
📱 За 15 минут до встречи пришлю напоминание
💬 Эксперт свяжется с тобой точно в назначенное время  
📝 Подготовь вопросы для максимальной пользы

💡 *Если планы изменятся — можешь перенести встречу заранее*

Увидимся на консультации! 👋"""
            
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(
                text="📅 Перенести консультацию",
                callback_data="consult:reschedule"
            ))
            keyboard.add(InlineKeyboardButton(
                text="💬 Задать вопросы до встречи",
                callback_data="llm:pre_consult_questions"
            ))
            keyboard.adjust(1)
            
            await callback.message.edit_text(
                confirmation_text,
                reply_markup=keyboard.as_markup(),
                parse_mode="Markdown"
            )
            
            # Log successful booking
            event_service = EventService(kwargs.get("session"))
            await event_service.log_consultation_booked(
                user_id=user.id,
                date=date_str,
                time=time_str
            )
            
            await callback.answer("✅ Консультация запланирована!")
            
        else:
            await callback.message.edit_text(
                f"❌ **Ошибка при записи**\n\n{message}\n\nПопробуй выбрать другое время или обратись к менеджеру.",
                parse_mode="Markdown"
            )
            await callback.answer("Ошибка при записи")
            
    except Exception as e:
        logger.error("Error selecting consultation time", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка при записи")


@router.callback_query(F.data == "consult:custom_date")
async def request_custom_date(callback: CallbackQuery, state: FSMContext, **kwargs):
    """Request custom date input."""
    try:
        await state.set_state(ConsultationStates.waiting_custom_date)
        
        custom_date_text = """📝 **Введи желаемую дату консультации**

Формат: ДД.ММ.ГГГГ (например: 25.12.2024)

⚠️ **Обрати внимание:**
• Консультации проводятся только в будние дни
• Доступное время: 12:00, 14:00, 16:00, 18:00 МСК
• Нельзя записаться на сегодня

Введи дату:"""
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="🔙 Назад к выбору дат",
            callback_data="consult:offer"
        ))
        
        await callback.message.edit_text(
            custom_date_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        await callback.answer()
        
    except Exception as e:
        logger.error("Error requesting custom date", error=str(e), exc_info=True)
        await callback.answer("Произошла ошибка")


@router.message(ConsultationStates.waiting_custom_date)
async def handle_custom_date(message: Message, user: User, state: FSMContext, **kwargs):
    """Handle custom date input."""
    try:
        session = kwargs.get("session")
        date_text = message.text.strip()
        
        # Parse date
        try:
            selected_date = datetime.strptime(date_text, "%d.%m.%Y").date()
        except ValueError:
            await message.answer(
                "❌ Неверный формат даты. Используй формат ДД.ММ.ГГГГ (например: 25.12.2024)"
            )
            return
        
        # Validate date
        if selected_date <= date.today():
            await message.answer(
                "❌ Нельзя записаться на прошедшую дату или сегодня. Выбери дату начиная с завтра."
            )
            return
        
        if selected_date.weekday() >= 5:
            await message.answer(
                "❌ Консультации проводятся только в будние дни (понедельник-пятница)."
            )
            return
        
        # Clear state
        await state.clear()
        
        # Show time slots for selected date
        consultation_service = ConsultationService(session)
        available_slots = await consultation_service.get_available_slots_for_date(selected_date)
        
        if not available_slots:
            await message.answer(
                f"❌ К сожалению, на {selected_date.strftime('%d.%m.%Y')} нет свободных слотов. Выбери другую дату."
            )
            return
        
        formatted_date = selected_date.strftime("%d.%m.%Y (%A)")
        
        time_text = f"""📅 **Дата выбрана: {formatted_date}**

⏰ **Выбери удобное время:**

{consultation_service.get_time_slots_text()}

Все время указано по Москве 🇷🇺"""
        
        keyboard = InlineKeyboardBuilder()
        
        for slot in available_slots:
            formatted_time = consultation_service.format_slot_time(slot)
            keyboard.add(InlineKeyboardButton(
                text=f"⏰ {formatted_time}",
                callback_data=f"consult:time:{selected_date.isoformat()}:{slot.isoformat()}"
            ))
        
        keyboard.add(InlineKeyboardButton(
            text="🔙 Выбрать другую дату",
            callback_data="consult:offer"
        ))
        keyboard.adjust(1)
        
        await message.answer(
            time_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error("Error handling custom date", error=str(e), user_id=user.id, exc_info=True)
        await message.answer("Произошла ошибка при обработке даты")


@router.callback_query(F.data == "consult:reschedule")
async def reschedule_consultation(callback: CallbackQuery, user: User, **kwargs):
    """Handle consultation rescheduling."""
    try:
        session = kwargs.get("session")
        consultation_service = ConsultationService(session)
        
        # Get user's upcoming appointment
        upcoming = await consultation_service.repository.get_upcoming_appointments(user.id)
        if not upcoming:
            await callback.answer("У вас нет запланированных консультаций")
            return
        
        appointment = upcoming[0]
        current_details = consultation_service.format_appointment_details(appointment)
        
        # Get available dates for rescheduling
        available_dates = consultation_service.get_next_available_dates(days_ahead=7)
        
        reschedule_text = f"""📅 **Перенос консультации**

**Текущая консультация:**
{current_details}

📅 **Выбери новую дату:**"""
        
        keyboard = InlineKeyboardBuilder()
        
        # Add date options
        for date_option in available_dates[:3]:
            formatted_date = date_option.strftime("%d.%m (%a)")
            keyboard.add(InlineKeyboardButton(
                text=f"📅 {formatted_date}",
                callback_data=f"reschedule:date:{date_option.isoformat()}:{appointment.id}"
            ))
        
        keyboard.add(InlineKeyboardButton(
            text="❌ Отменить консультацию",
            callback_data="consult:cancel"
        ))
        keyboard.add(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back:main_menu"
        ))
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            reschedule_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        await callback.answer()
        
    except Exception as e:
        logger.error("Error rescheduling consultation", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка")


@router.callback_query(F.data == "consult:cancel")
async def cancel_consultation(callback: CallbackQuery, user: User, **kwargs):
    """Handle consultation cancellation."""
    try:
        session = kwargs.get("session")
        consultation_service = ConsultationService(session)
        
        # Get user's upcoming appointment
        upcoming = await consultation_service.repository.get_upcoming_appointments(user.id)
        if not upcoming:
            await callback.answer("У вас нет запланированных консультаций")
            return
        
        appointment = upcoming[0]
        
        # Cancel appointment
        success = await consultation_service.cancel_appointment(appointment)
        
        if success:
            cancel_text = f"""❌ **Консультация отменена**

Твоя консультация на {appointment.date.strftime('%d.%m.%Y')} в {appointment.slot.strftime('%H:%M')} МСК была отменена.

💭 **Если передумаешь — всегда можешь записаться снова!**

Нужна помощь? Обратись к менеджеру 👤"""
            
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(
                text="📞 Записаться на новую консультацию",
                callback_data="consult:offer"
            ))
            keyboard.add(InlineKeyboardButton(
                text="👤 Связаться с менеджером",
                callback_data="manager:request"
            ))
            keyboard.adjust(1)
            
            await callback.message.edit_text(
                cancel_text,
                reply_markup=keyboard.as_markup(),
                parse_mode="Markdown"
            )
            
            await callback.answer("✅ Консультация отменена")
            
        else:
            await callback.answer("Произошла ошибка при отмене")
            
    except Exception as e:
        logger.error("Error canceling consultation", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка")


def register_handlers(dp):
    """Register consultation handlers."""
    dp.include_router(router)