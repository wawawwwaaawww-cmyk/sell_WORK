"""Handlers for the manual application submission flow."""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Appointment, FunnelStage, User
from app.services.consultation_service import ConsultationService
from app.services.event_service import EventService
from app.services.lead_service import LeadService
from app.services.manager_notification_service import ManagerNotificationService
from app.services.survey_service import SurveyService
from app.services.user_service import UserService
from app.services.sales_script_service import SalesScriptService
from app.utils.callbacks import Callbacks
from app.repositories.user_repository import UserRepository
from app.config import settings


router = Router()
logger = structlog.get_logger()


async def _refresh_sales_script(session, bot, user: User, reason: str) -> None:
    if not settings.sales_script_enabled or session is None:
        return
    try:
        service = SalesScriptService(session, bot)
        await service.refresh_for_user(user, reason=reason, bot=bot)
    except Exception as exc:  # pragma: no cover
        logger.warning(
            "sales_script_refresh_failed",
            user_id=user.id,
            reason=reason,
            error=str(exc),
        )


class ApplicationStates(StatesGroup):
    """FSM states for the application form."""

    waiting_phone = State()
    waiting_name = State()
    waiting_email = State()


_QUESTION_LABELS: Dict[str, str] = {
    "q1": "🎯 Опыт",
    "q2": "💡 Цель",
    "q3": "🛡️ Риск-профиль",
    "q4": "⏰ Вовлеченность",
    "q5": "💰 Бюджет",
}

_STAGE_LABELS: Dict[FunnelStage, str] = {
    FunnelStage.NEW: "новый контакт",
    FunnelStage.WELCOMED: "получил приветствие",
    FunnelStage.SURVEYED: "анкета пройдена",
    FunnelStage.ENGAGED: "активно общается с ботом",
    FunnelStage.QUALIFIED: "готов к консультации",
    FunnelStage.CONSULTATION: "назначена консультация",
    FunnelStage.PAYMENT: "на этапе оплаты",
    FunnelStage.PAID: "оплата получена",
    FunnelStage.INACTIVE: "неактивен",
}


def _normalize_phone(phone: str) -> Optional[Tuple[str, str]]:
    """Normalize phone number, returning digits-only and display variants."""
    raw = phone or ""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None

    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if len(digits) == 10 and digits.startswith("9"):
        digits = "7" + digits

    normalized = f"+{digits}"
    display = normalized

    if len(digits) == 11 and digits.startswith("7"):
        display = f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:]}"

    return normalized, display


async def _collect_survey_data(
    survey_service: SurveyService,
    user_id: int,
) -> List[Tuple[str, str]]:
    """Collect survey answers formatted for the notification card."""
    answers = await survey_service.repository.get_user_answers(user_id)
    answer_map: Dict[str, str] = {answer.question_code: answer.answer_code for answer in answers}

    ordered_questions: Sequence[str] = ("q1", "q2", "q3", "q4", "q5")
    results: List[Tuple[str, str]] = []

    for code in ordered_questions:
        answer_code = answer_map.get(code)
        if not answer_code:
            continue
        question = survey_service.questions.get(code)
        if not question:
            continue
        option = question["options"].get(answer_code)
        if not option:
            continue
        label = _QUESTION_LABELS.get(code, question["text"])
        results.append((label, option["text"]))

    return results


def _build_status_text(user: User, appointment: Optional[Appointment]) -> str:
    """Create status description for the manager card."""
    if appointment:
        dt = datetime.combine(appointment.date, appointment.slot)
        return f"назначена консультация ({dt.strftime('%d.%m.%Y %H:%M')} МСК)"
    return _STAGE_LABELS.get(user.funnel_stage, "на связи с ботом")


def _build_telegram_html(user: User) -> str:
    """Return Telegram identity as HTML."""
    if user.username:
        return html.escape(f"@{user.username}")
    return f'<a href="tg://user?id={user.telegram_id}">профиль</a>'


def _build_lead_summary(
    name: str,
    phone_display: str,
    email: Optional[str],
    survey_data: List[Tuple[str, str]],
    status_text: str,
) -> str:
    """Create lead summary stored in DB."""
    parts = [
        f"Имя: {name}",
        f"Телефон: {phone_display}",
        f"Email: {email or 'не указан'}",
    ]

    if survey_data:
        parts.append("Ответы анкеты:")
        parts.extend(f"- {label}: {answer}" for label, answer in survey_data)

    parts.append(f"Статус: {status_text}")
    return "\n".join(parts)


async def _begin_application_flow(
    message: Message,
    state: FSMContext,
    user: User,
    session,
) -> None:
    """Send initial prompt and set FSM state."""
    await state.clear()

    intro_text = (
        "🎯 Отлично! У вас есть возможность пообщаться с экспертом для определения инструмента инвестирования, который подходит именно вам\n"
        "👉 Для записи, оставьте, пожалуйста, ваш контактный номер телефона.\n"
        "Можно ввести вручную или нажать на кнопку «Поделиться контактом» — поддерживаю оба варианта."
    )

    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться контактом", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )

    await message.answer(intro_text, reply_markup=keyboard)

    if session:
        event_service = EventService(session)
        await event_service.log_event(
            user_id=user.id,
            event_type="application_started",
            payload={},
        )

    await state.set_state(ApplicationStates.waiting_phone)


async def _finalize_application(
    message: Message,
    user: User,
    *,
    state: FSMContext,
    session,
    name: str,
    phone_normalized: str,
    phone_display: str,
    email: Optional[str],
) -> None:
    """Create lead, notify managers and thank the user."""
    survey_service = SurveyService(session)
    lead_service = LeadService(session)
    consultation_service = ConsultationService(session)
    manager_service = ManagerNotificationService(message.bot, session)
    event_service = EventService(session)

    survey_data = await _collect_survey_data(survey_service, user.id)
    survey_lines_html = [f"{html.escape(label)}: {html.escape(answer)}" for label, answer in survey_data]

    upcoming = await consultation_service.repository.get_upcoming_appointments(user.id)
    appointment = upcoming[0] if upcoming else None
    status_text = _build_status_text(user, appointment)

    lead_summary = _build_lead_summary(
        name=name,
        phone_display=phone_display,
        email=email,
        survey_data=survey_data,
        status_text=status_text,
    )

    lead = None
    try:
        lead = await lead_service.create_lead(
            user_id=user.id,
            summary=lead_summary,
            trigger="application_form",
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to create lead for application", error=str(exc), user_id=user.id, exc_info=True)

    telegram_html = _build_telegram_html(user)
    try:
        await manager_service.notify_new_application(
            user=user,
            name=name,
            phone=phone_display,
            telegram_html=telegram_html,
            email=email,
            survey_lines=survey_lines_html,
            status=status_text,
            lead_id=getattr(lead, "id", None),
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to send manager notification for application", error=str(exc), user_id=user.id, exc_info=True)

    await event_service.log_event(
        user_id=user.id,
        event_type="application_submitted",
        payload={
            "name": name,
            "phone": phone_normalized,
            "email": email or "",
            "survey_answers": survey_data,
            "status": status_text,
            "lead_id": getattr(lead, "id", None),
        },
    )

    await state.clear()

    thank_you_text = "Спасибо, ваша заявка принята и мы скоро свяжемся с Вами😉"
    await message.answer(thank_you_text, reply_markup=ReplyKeyboardRemove())


@router.callback_query(F.data == Callbacks.APPLICATION_START)
async def start_application(
    callback: CallbackQuery,
    state: FSMContext,
    user: User,
    **kwargs,
):
    """Entry point for the application form."""
    try:
        if callback.message is None:
            await callback.answer("Произошла ошибка", show_alert=True)
            return

        await _begin_application_flow(
            callback.message,
            state,
            user,
            kwargs.get("session"),
        )
        await callback.answer()

    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to start application flow", error=str(exc), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка", show_alert=True)


@router.message(Command("contact"))
async def contact_command(
    message: Message,
    state: FSMContext,
    user: User,
    **kwargs,
):
    """Start application form via /contact command."""
    try:
        await _begin_application_flow(
            message,
            state,
            user,
            kwargs.get("session"),
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to start application via command", error=str(exc), user_id=user.id, exc_info=True)
        await message.answer("Произошла ошибка. Попробуйте позднее.")


@router.message(ApplicationStates.waiting_phone, F.contact)
async def handle_contact_phone(
    message: Message,
    state: FSMContext,
    user: User,
    user_service: UserService,
    **kwargs,
):
    """Handle contact sharing with phone number."""
    contact = message.contact
    if contact is None or (contact.user_id and contact.user_id != message.from_user.id):
        await message.answer("Похоже, контакт не принадлежит вам. Введите номер вручную, пожалуйста.")
        return

    normalization = _normalize_phone(contact.phone_number)
    if not normalization:
        await message.answer("Не получилось распознать номер. Введите его вручную, пожалуйста.")
        return

    phone_normalized, phone_display = normalization
    await state.update_data(phone=phone_normalized, phone_display=phone_display)

    await user_service.set_user_contact_info(user, phone=phone_normalized)
    await _refresh_sales_script(kwargs.get("session"), message.bot, user, "application_phone_manual")
    await _refresh_sales_script(kwargs.get("session"), message.bot, user, "application_phone_contact")
    await state.set_state(ApplicationStates.waiting_name)

    await message.answer(
        "✏️ Напишите, как к вам обращаться:",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ApplicationStates.waiting_phone)
async def handle_text_phone(
    message: Message,
    state: FSMContext,
    user: User,
    user_service: UserService,
    **kwargs,
):
    """Handle manually entered phone number."""
    normalization = _normalize_phone(message.text or "")
    if not normalization:
        await message.answer("Не получилось распознать номер. Попробуйте ввести его в формате +7 ХХХ ХХХ-ХХ-ХХ.")
        return

    phone_normalized, phone_display = normalization
    await state.update_data(phone=phone_normalized, phone_display=phone_display)

    await user_service.set_user_contact_info(user, phone=phone_normalized)
    await state.set_state(ApplicationStates.waiting_name)

    await message.answer(
        "✏️ Напишите, как к вам обращаться:",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(ApplicationStates.waiting_name)
async def handle_name_input(
    message: Message,
    state: FSMContext,
    **_kwargs,
):
    """Save provided name and prompt for email."""
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Пожалуйста, укажите имя, чтобы я мог правильно к вам обращаться.")
        return

    await state.update_data(name=name)
    await state.set_state(ApplicationStates.waiting_email)

    prompt_text = (
        "Хотите, чтобы я продублировал информацию ещё и на почту?\n"
        "Для этого пункта есть кнопка «Пропустить», и тогда заявка сохранится без почты.\n\n"
        "Введите ваш email:"
    )

    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="Пропустить", callback_data=Callbacks.APPLICATION_SKIP_EMAIL))

    await message.answer(prompt_text, reply_markup=keyboard.as_markup())


def _is_valid_email(value: str) -> bool:
    """Basic email validation for user input."""
    if not value or "@" not in value:
        return False
    pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    return re.match(pattern, value) is not None


@router.message(ApplicationStates.waiting_email)
async def handle_email_input(
    message: Message,
    state: FSMContext,
    user: User,
    user_service: UserService,
    **kwargs,
):
    """Process provided email and finalize application."""
    email_candidate = (message.text or "").strip()

    if not _is_valid_email(email_candidate):
        await message.answer("Похоже, email указан в неверном формате. Попробуйте ещё раз или нажмите «Пропустить».")
        return

    await user_service.set_user_contact_info(user, email=email_candidate)
    await _refresh_sales_script(kwargs.get("session"), message.bot, user, "application_email")

    data = await state.get_data()
    name = data.get("name")
    phone_normalized = data.get("phone")
    phone_display = data.get("phone_display")

    if not all([name, phone_normalized, phone_display]):
        await message.answer("Не хватает данных для заявки. Начните, пожалуйста, заново.")
        await state.clear()
        return

    await _finalize_application(
        message,
        user,
        state=state,
        session=kwargs.get("session"),
        name=name,
        phone_normalized=phone_normalized,
        phone_display=phone_display,
        email=email_candidate,
    )


@router.callback_query(ApplicationStates.waiting_email, F.data == Callbacks.APPLICATION_SKIP_EMAIL)
async def skip_email(
    callback: CallbackQuery,
    state: FSMContext,
    user: User,
    **kwargs,
):
    """Skip email capture and finalize application."""
    await callback.answer("Заявка сохранена без почты")

    data = await state.get_data()
    name = data.get("name")
    phone_normalized = data.get("phone")
    phone_display = data.get("phone_display")

    if callback.message:
        try:
            await callback.message.edit_reply_markup()
        except Exception:  # pragma: no cover - cleanup step
            pass

    if not all([name, phone_normalized, phone_display]):
        if callback.message:
            await callback.message.answer("Похоже, данных не хватает. Начните заново, пожалуйста.")
        await state.clear()
        return

    await _finalize_application(
        callback.message,
        user,
        state=state,
        session=kwargs.get("session"),
        name=name,
        phone_normalized=phone_normalized,
        phone_display=phone_display,
        email=None,
    )


@router.callback_query(F.data.startswith(f"{Callbacks.APPLICATION_TAKE}:"))
async def handle_application_take(
    callback: CallbackQuery,
    **kwargs,
):
    """Assign application to the manager who clicked the button."""
    try:
        parts = callback.data.split(":")
        if len(parts) < 4:
            await callback.answer("Некорректные данные", show_alert=True)
            return

        lead_id = int(parts[2])
        user_id = int(parts[3])
        manager_id = callback.from_user.id
        session = kwargs.get("session")

        lead_service = LeadService(session)
        lead = await lead_service.repository.get_lead_by_id(lead_id)
        if not lead:
            await callback.answer("Заявка не найдена", show_alert=True)
            return

        success, message = await lead_service.assign_lead(lead_id, manager_id)
        if not success:
            await callback.answer(message, show_alert=True)
            return

        # Refresh lead data with updated assignment
        lead = await lead_service.repository.get_lead_by_id(lead_id)

        user_repo = UserRepository(session)
        applicant = await user_repo.get_by_id(user_id)

        # Remove button from channel message
        if callback.message:
            try:
                await callback.message.edit_reply_markup()
            except Exception:  # pragma: no cover - message might be already updated
                pass

        # Send full application text to manager's DM
        dm_text = ""
        if callback.message:
            dm_text = getattr(callback.message, "html_text", None) or callback.message.text or ""
        if dm_text:
            dm_text = f"{dm_text}\n\n✅ Заявка назначена на вас."
            try:
                await callback.bot.send_message(
                    chat_id=manager_id,
                    text=dm_text,
                    parse_mode="HTML",
                )
            except Exception as exc:  # pylint: disable=broad-except
                logger.error(
                    "Failed to send application DM",
                    error=str(exc),
                    manager_id=manager_id,
                    lead_id=lead_id,
                    exc_info=True,
                )

        # Log event
        if session and applicant:
            event_service = EventService(session)
            await event_service.log_event(
                user_id=applicant.id,
                event_type="application_taken",
                payload={
                    "lead_id": lead_id,
                    "manager_id": manager_id,
                },
            )

        # Notify channel about assignment
        manager_display = f"@{callback.from_user.username}" if callback.from_user.username else (
            callback.from_user.full_name or f"ID {manager_id}"
        )
        channel_text = (
            f"Менеджер {manager_display} взял заявку ID заявки: {lead_id}"
        )
        try:
            await callback.bot.send_message(
                chat_id=settings.manager_channel_id,
                text=channel_text,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.error(
                "Failed to send channel assignment notice",
                error=str(exc),
                manager_id=manager_id,
                lead_id=lead_id,
                exc_info=True,
            )

        await callback.answer("Заявка назначена на вас!")

    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Error taking application", error=str(exc), exc_info=True)
        await callback.answer("Произошла ошибка", show_alert=True)


def register_handlers(dp) -> None:
    """Register application handlers with dispatcher."""
    dp.include_router(router)
