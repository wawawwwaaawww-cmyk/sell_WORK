"""Consultation scheduling handlers."""

from datetime import date, time, datetime
from typing import Dict, Any

import structlog
from aiogram import Router, F, Bot
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from app.models import User, FunnelStage
from app.services.user_service import UserService
from app.services.consultation_service import ConsultationService
from app.services.event_service import EventService
from app.services.manager_notification_service import ManagerNotificationService
from app.services.lead_service import LeadService
from app.utils.callbacks import Callbacks


class ConsultationStates(StatesGroup):
    """FSM for consultation booking."""

    choosing_date = State()
    choosing_time = State()
    free_form_datetime = State()
    confirmation = State()
    waiting_for_phone = State()
    waiting_for_name = State()


router = Router()
logger = structlog.get_logger()


async def start_consultation_booking(
    message: Message, state: FSMContext, user: User, session
):
    """Starts the consultation booking flow."""
    consultation_service = ConsultationService(session)
    date_options = consultation_service.get_consultation_date_options()

    keyboard = InlineKeyboardBuilder()
    for option in date_options:
        keyboard.add(
            InlineKeyboardButton(
                text=option["label"],
                callback_data=f"consult_date:{option['date'].isoformat()}",
            )
        )
    keyboard.add(
        InlineKeyboardButton(
            text="Нет подходящей даты", callback_data="consult_date:custom"
        )
    )
    keyboard.adjust(1)

    await message.answer(
        "📅 Выберите удобную дату для консультации (время московское):",
        reply_markup=keyboard.as_markup(),
    )
    await state.set_state(ConsultationStates.choosing_date)


@router.callback_query(F.data.startswith("consult_date:"))
async def handle_date_choice(
    callback: CallbackQuery, state: FSMContext, session, user: User, **kwargs
):
    """Handles the user's choice of a consultation date."""
    await callback.answer()
    choice = callback.data.split(":")[1]

    lead_service = LeadService(session)
    await lead_service.start_incomplete_lead_timer(user, "consultation_date_picked")

    if choice == "custom":
        await callback.message.edit_text(
            "Напишите желаемую дату и время одним сообщением (например, 'завтра в 14:30' или '15.10 18:00').\n\nМы постараемся подобрать для вас удобный слот."
        )
        await state.set_state(ConsultationStates.free_form_datetime)
        return

    selected_date = date.fromisoformat(choice)
    await state.update_data(selected_date=selected_date.isoformat())

    consultation_service = ConsultationService(session)
    time_slots = consultation_service.available_slots

    keyboard = InlineKeyboardBuilder()
    for slot in time_slots:
        keyboard.add(
            InlineKeyboardButton(
                text=slot.strftime("%H:%M"),
                callback_data=f"consult_time:{slot.isoformat()}",
            )
        )
    keyboard.add(
        InlineKeyboardButton(
            text="Нет подходящего времени", callback_data="consult_time:custom"
        )
    )
    keyboard.adjust(2)

    await callback.message.edit_text(
        f"Вы выбрали: {selected_date.strftime('%d %B (%a)')}. Теперь выберите удобное время (МСК):",
        reply_markup=keyboard.as_markup(),
    )
    await state.set_state(ConsultationStates.choosing_time)


@router.callback_query(F.data.startswith("consult_time:"))
async def handle_time_choice(callback: CallbackQuery, state: FSMContext, session, user: User, **kwargs):
    """Handles the user's choice of a consultation time."""
    await callback.answer()
    choice = callback.data.split(":")[1]

    lead_service = LeadService(session)
    await lead_service.start_incomplete_lead_timer(user, "consultation_time_picked")

    if choice == "custom":
        await callback.message.edit_text(
            "Напишите желаемую дату и время одним сообщением (например, 'завтра в 14:30' или '15.10 18:00').\n\nМы постараемся подобрать для вас удобный слот."
        )
        await state.set_state(ConsultationStates.free_form_datetime)
        return

    selected_time = time.fromisoformat(choice)
    user_data = await state.get_data()
    selected_date = date.fromisoformat(user_data["selected_date"])

    await state.update_data(
        selected_time=selected_time.isoformat(), final_date=selected_date.isoformat()
    )

    await show_confirmation(callback.message, state)
    await state.set_state(ConsultationStates.confirmation)


@router.message(ConsultationStates.free_form_datetime)
async def handle_free_form_datetime(
    message: Message, state: FSMContext, session, **kwargs
):
    """Handles free-form date and time input."""
    consultation_service = ConsultationService(session)
    parsed_dt, error_message = consultation_service.parse_free_text_datetime(
        message.text
    )

    if not parsed_dt:
        user_data = await state.get_data()
        attempts = user_data.get("free_form_attempts", 0) + 1
        await state.update_data(free_form_attempts=attempts)

        if attempts >= 2:
            # TODO: Implement manager handoff
            await message.answer(
                f"К сожалению, не удалось распознать время. Давайте я соединю вас с менеджером, чтобы он подобрал удобное время.\n\n{error_message}"
            )
            await state.clear()
        else:
            await message.answer(
                f"Не удалось распознать дату и время. Попробуйте еще раз.\n\n{error_message}"
            )
        return

    await state.update_data(
        final_date=parsed_dt.date().isoformat(),
        selected_time=parsed_dt.time().isoformat(),
    )
    await show_confirmation(message, state)
    await state.set_state(ConsultationStates.confirmation)


async def show_confirmation(message: Message, state: FSMContext):
    """Shows the confirmation message and buttons."""
    user_data = await state.get_data()
    final_date = date.fromisoformat(user_data["final_date"])
    selected_time = time.fromisoformat(user_data["selected_time"])

    confirmation_text = f"Вы выбрали: {final_date.strftime('%d %B (%A)')}, {selected_time.strftime('%H:%M')} МСК. Всё верно?"

    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="consult_confirm:yes")
    )
    keyboard.add(
        InlineKeyboardButton(text="✏️ Изменить", callback_data="consult_confirm:no")
    )

    await message.answer(confirmation_text, reply_markup=keyboard.as_markup())


@router.callback_query(F.data.startswith("consult_confirm:"))
async def handle_confirmation(
    callback: CallbackQuery,
    state: FSMContext,
    user: User,
    session,
    bot: Bot,
    user_service: UserService,
    **kwargs,
):
    """Handles the final confirmation of the consultation slot."""
    await callback.answer()
    choice = callback.data.split(":")[1]

    if choice == "no":
        await callback.message.delete()
        await start_consultation_booking(callback.message, state, user, session)
        return

    if not user.phone:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Отправить телефон", request_contact=True)]
            ],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await callback.message.delete()
        await callback.message.answer(
            "Для подтверждения записи, пожалуйста, отправьте ваш номер телефона.",
            reply_markup=keyboard,
        )
        await state.set_state(ConsultationStates.waiting_for_phone)
    else:
        await create_appointment(callback.message, state, user, session, bot, user_service)


@router.message(F.contact, ConsultationStates.waiting_for_phone)
async def handle_phone_contact(
    message: Message,
    state: FSMContext,
    user: User,
    session,
    bot: Bot,
    user_service: UserService,
    **kwargs,
):
    """Handles receiving the user's phone number."""
    user.phone = message.contact.phone_number
    await user_service.set_user_contact_info(user, phone=user.phone)
    await session.commit()
    
    lead_service = LeadService(session)
    await lead_service.start_incomplete_lead_timer(user, "consultation_phone_sent")

    await message.answer("Пожалуйста, напишите ваше имя.")
    await state.set_state(ConsultationStates.waiting_for_name)


@router.message(ConsultationStates.waiting_for_name)
async def handle_name(
    message: Message,
    state: FSMContext,
    user: User,
    session,
    bot: Bot,
    user_service: UserService,
    **kwargs,
):
    """Handles receiving the user's name."""
    await state.update_data(user_name=message.text)
    await create_appointment(message, state, user, session, bot, user_service)


async def create_appointment(
    message: Message,
    state: FSMContext,
    user: User,
    session,
    bot: Bot,
    user_service: UserService,
):
    """Creates the appointment and notifies the user and managers."""
    user_data = await state.get_data()
    final_date = date.fromisoformat(user_data["final_date"])
    selected_time = time.fromisoformat(user_data["selected_time"])

    consultation_service = ConsultationService(session)
    success, appointment, error_msg = await consultation_service.book_consultation(
        user_id=user.id,
        user_name=user_data.get("user_name") or user.first_name or "Пользователь",
        consultation_date=final_date,
        slot=selected_time,
        source="bot_survey",
    )

    if not success:
        await message.answer(f"❌ Произошла ошибка при записи: {error_msg}")
        await state.clear()
        return

    await user_service.advance_funnel_stage(user, FunnelStage.CONSULTATION)
    event_service = EventService(session)
    await event_service.log_consultation_booked(
        user_id=user.id,
        date=final_date.isoformat(),
        time=selected_time.isoformat(),
    )

    confirmation_text = (
        f"✅ **Отлично!**\n\n"
        f"Вы записаны на консультацию **{final_date.strftime('%d %B (%A)')} в {selected_time.strftime('%H:%M')} по МСК**.\n\n"
        f"За 15 минут до начала я пришлю напоминание."
    )
    await message.answer(confirmation_text, parse_mode="Markdown")

    # Notify managers
    try:
        manager_notifier = ManagerNotificationService(bot, session)
        await manager_notifier.notify_new_consultation(appointment)
    except Exception as e:
        logger.error("Failed to notify managers about new consultation", error=e, appointment_id=appointment.id)

    await state.clear()


# Handlers for reminders
@router.callback_query(F.data.startswith("consult_reminder:"))
async def handle_reminder_response(
    callback: CallbackQuery, state: FSMContext, user: User, session, bot: Bot, **kwargs
):
    """Handles user's response to the 15-minute reminder."""
    await callback.answer()
    parts = callback.data.split(":")
    action = parts[1]
    appointment_id = int(parts[2])

    consultation_service = ConsultationService(session)
    appointment = await consultation_service.process_reminder_response(
        appointment_id, action
    )

    if not appointment or appointment.user_id != user.id:
        await callback.message.edit_text("Не удалось найти вашу запись. Возможно, она была отменена.")
        return

    event_service = EventService(session)
    await event_service.log_consultation_reminder_response(
        user_id=user.id, appointment_id=appointment_id, response=action
    )

    manager_notifier = ManagerNotificationService(bot, session)

    if action == "confirm":
        await callback.message.edit_text("👍 Отлично, ждем вас на консультации!")
        await manager_notifier.notify_consultation_confirmed(appointment)
    elif action == "reschedule":
        # Cancel the old appointment before booking a new one
        await consultation_service.cancel_appointment(appointment)
        await callback.message.edit_text("Чтобы перенести консультацию, давайте выберем новую дату.")
        await start_consultation_booking(callback.message, state, user, session)
        await manager_notifier.notify_consultation_rescheduled(appointment)
    elif action == "cancel":
        await callback.message.edit_text("Жаль, что у вас не получается. Консультация отменена.")
        await manager_notifier.notify_consultation_cancelled(appointment)


def register_handlers(dp):
    """Register consultation handlers."""
    dp.include_router(router)