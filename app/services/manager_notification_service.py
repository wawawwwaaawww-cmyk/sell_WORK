"""Service for sending notifications to managers."""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import List, Optional, Sequence, Tuple
try:  # pragma: no cover - Python <3.9 fallback
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment,misc]

import structlog
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Appointment, FunnelStage, Lead, LeadStatus, User
from app.services.lead_service import LeadService
from app.services.sales_script_service import SalesScriptService
from app.services.survey_service import SurveyService
from app.services.user_service import UserService
from app.utils.callbacks import Callbacks

_QUESTION_LABELS: dict[str, str] = {
    "q1": "🎯 Опыт",
    "q2": "💡 Цель",
    "q3": "🛡️ Риск-профиль",
    "q4": "⏰ Вовлеченность",
    "q5": "💰 Бюджет",
}

_STAGE_LABELS: dict[FunnelStage, str] = {
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

_SEGMENT_LABELS: dict[str, str] = {
    "cold": "cold",
    "warm": "warm",
    "hot": "hot",
}


class ManagerNotificationService:
    """Service to handle notifications to managers."""

    def __init__(self, bot: Bot, session: AsyncSession):
        self.bot = bot
        self.session = session
        self.logger = structlog.get_logger()
        self.manager_channel_id = settings.manager_channel_id

    async def _get_user_info(self, user_id: int) -> User:
        """Retrieve user information from the database."""
        user = await self.session.get(User, user_id)
        return user

    def _sales_scripts(self) -> SalesScriptService:
        return SalesScriptService(self.session, self.bot)

    async def notify_new_lead(self, lead: Lead, user: User) -> Optional[int]:
        """Notify managers about a freshly created lead."""
        if not self.manager_channel_id:
            self.logger.warning("Manager channel ID is not configured. Skipping notification.")
            return None

        lead_service = LeadService(self.session)
        card_text = await lead_service.format_lead_card(lead, user)

        keyboard = self._build_lead_channel_keyboard(lead.id, user.id)
        message = await self.bot.send_message(
            chat_id=self.manager_channel_id,
            text=card_text,
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

        if settings.sales_script_enabled:
            script_service = self._sales_scripts()
            try:
                await script_service.ensure_script(
                    lead,
                    user,
                    reason="lead_card_publish",
                )
                await script_service.log_lead_card_posted(
                    lead.id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                )
            except Exception as exc:  # pragma: no cover
                self.logger.warning(
                    "sales_script_prepare_failed",
                    lead_id=lead.id,
                    error=str(exc),
                )
        return message.message_id

    async def notify_lead_taken(self, lead: Lead, user: User, manager_telegram_id: int) -> None:
        """Notify a manager that the lead has been assigned and deliver script copy."""
        preview = (
            f"✅ Вы взяли заявку #{lead.id}.\n"
            f"Клиент: {user.first_name or user.username or user.telegram_id}"
        )
        await self.bot.send_message(manager_telegram_id, preview)

        if settings.sales_script_enabled and settings.sales_script_send_to_manager_on_assign:
            script_service = self._sales_scripts()
            try:
                result = await script_service.ensure_script(
                    lead,
                    user,
                    reason="lead_assigned",
                    actor_id=manager_telegram_id,
                )
                await script_service.send_script_to_manager(
                    lead,
                    user,
                    result,
                    manager_telegram_id=manager_telegram_id,
                    include_preview=False,
                )
            except Exception as exc:  # pragma: no cover
                self.logger.error(
                    "sales_script_send_failed",
                    lead_id=lead.id,
                    manager_id=manager_telegram_id,
                    error=str(exc),
                )

    async def _format_message(self, appointment: Appointment, user: User, title: str) -> str:
        """Format a short notification message (used for status updates)."""
        title = title.strip()
        user_info = f"Пользователь: {appointment.user_name or user.first_name or ''}".strip()
        if user.username:
            user_info += f" (@{user.username})"

        phone_info = f"Телефон: {user.phone}" if user.phone else "Телефон: не указан"

        slot_msk = f"{appointment.date.strftime('%d.%m.%Y')} в {appointment.slot.strftime('%H:%M')} МСК"

        return (
            f"<b>{title}</b>\n\n"
            f"🗓 {slot_msk}\n"
            f"👤 {html.escape(user_info)}\n"
            f"📞 {html.escape(phone_info)}\n"
            f"📊 Сегмент: {html.escape(user.segment or 'N/A')}\n"
            f"📈 Баллы: {user.lead_score or 0}"
        )

    async def notify_new_consultation(self, appointment: Appointment):
        """Notify managers about a new consultation with full lead context."""
        if not self.manager_channel_id:
            self.logger.warning("Manager channel ID is not configured. Skipping notification.")
            return

        user = await self._get_user_info(appointment.user_id)
        if not user:
            self.logger.warning("User not found for appointment", appointment_id=appointment.id)
            return

        consultation_dt = datetime.combine(appointment.date, appointment.slot)

        survey_pairs = await self._collect_survey_pairs(user.id)
        survey_lines = [
            f"{html.escape(label)}: {html.escape(answer)}" for label, answer in survey_pairs
        ]

        lead_obj: Optional[Lead] = None
        if settings.sales_script_enabled and lead_id:
            lead_obj = await self.session.get(Lead, lead_id)
            if lead_obj:
                try:
                    await self._sales_scripts().ensure_script(
                        lead_obj,
                        user,
                        reason="application_card",
                    )
                except Exception as exc:  # pragma: no cover
                    self.logger.warning(
                        "sales_script_prepare_failed",
                        lead_id=lead_id,
                        error=str(exc),
                    )

        lead_obj: Optional[Lead] = None
        script_service: Optional[SalesScriptService] = None
        if settings.sales_script_enabled and lead_id:
            script_service = self._sales_scripts()
            lead_obj = await self.session.get(Lead, lead_id)
            if lead_obj:
                try:
                    await script_service.ensure_script(
                        lead_obj,
                        user,
                        reason="application_card",
                    )
                except Exception as exc:  # pragma: no cover
                    self.logger.warning(
                        "sales_script_prepare_failed",
                        lead_id=lead_id,
                        error=str(exc),
                    )

        segment_label, lead_score_value = await self._resolve_segment_and_score(user)
        phone_display = self._format_phone_display(user.phone)
        telegram_html = self._build_telegram_html(user)
        email_value = user.email or "не указан"
        status_text = self._build_status_text(user, consultation_dt)
        lead_summary = self._build_lead_summary(
            name=appointment.user_name or user.first_name or "Не указано",
            phone_display=phone_display,
            email=email_value,
            survey_data=survey_pairs,
            status_text=status_text,
            segment=segment_label,
            lead_score=lead_score_value,
            consultation_dt=consultation_dt,
        )

        lead_id = await self._ensure_active_lead(user, lead_summary, trigger="consultation_booked")

        message_text = self._build_application_card(
            title="🆕 Новая консультация",
            name=appointment.user_name or user.first_name or "Не указано",
            phone_display=phone_display,
            telegram_html=telegram_html,
            email=email_value,
            survey_lines=survey_lines,
            status_text=status_text,
            segment=segment_label,
            lead_score=lead_score_value,
            consultation_dt=consultation_dt,
        )

        lead_obj: Optional[Lead] = None
        script_service: Optional[SalesScriptService] = None
        if settings.sales_script_enabled and lead_id:
            script_service = self._sales_scripts()
            lead_obj = await self.session.get(Lead, lead_id)
            if lead_obj:
                try:
                    await script_service.ensure_script(
                        lead_obj,
                        user,
                        reason="consultation_card",
                    )
                except Exception as exc:  # pragma: no cover
                    self.logger.warning(
                        "sales_script_prepare_failed",
                        lead_id=lead_id,
                        error=str(exc),
                    )

        keyboard = self._build_take_button_markup(lead_id, user.id)

        message = None
        try:
            message = await self.bot.send_message(
                chat_id=self.manager_channel_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as e:
            self.logger.error("Failed to send new consultation notification", error=e)
        else:
            if lead_obj and message and script_service:
                await script_service.log_lead_card_posted(
                    lead_obj.id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                )

    async def notify_manager_request(self, user: User) -> None:
        """Send manager request notification with recent user context."""
        if not self.manager_channel_id:
            self.logger.warning(
                "Manager channel ID is not configured. Skipping manager request notification.",
            )
            return

        user_service = UserService(self.session)
        display_name = user_service.get_user_display_name(user)
        phone_display = self._format_phone_display(user.phone)
        telegram_html = self._build_telegram_html(user)
        segment_label, lead_score_value = await self._resolve_segment_and_score(user)

        history = await user_service.get_conversation_history(user.id, limit=50)
        user_messages = [
            item for item in history if str(item.get("role", "")).lower() == "user"
        ]
        recent_messages = user_messages[-5:]

        tz_info: Optional[ZoneInfo] = None
        if ZoneInfo and settings.scheduler_timezone:
            try:
                tz_info = ZoneInfo(settings.scheduler_timezone)
            except Exception:  # pragma: no cover - invalid timezone config
                tz_info = None

        lines = [
            "📞 <b>Пользователь запросил менеджера</b>",
            "",
            f"Имя: {html.escape(display_name)}",
            f"Telegram: {telegram_html}",
            f"Телефон: {html.escape(phone_display)}",
            f"Email: {html.escape(user.email or 'не указан')}",
            f"Сегмент: {html.escape(segment_label)}",
            f"Баллы: {lead_score_value}",
            "",
            "<b>Последние сообщения пользователя:</b>",
        ]

        if recent_messages:
            for entry in recent_messages:
                timestamp = entry.get("timestamp")
                payload = entry.get("text") or ""
                safe_text = html.escape(self._shorten(payload.strip()))

                time_label = ""
                if isinstance(timestamp, datetime):
                    try:
                        ts = timestamp.astimezone(tz_info) if tz_info else timestamp.astimezone()
                        time_label = ts.strftime("%d.%m %H:%M")
                    except Exception:  # pragma: no cover - defensive
                        time_label = ""

                if time_label:
                    lines.append(f"{time_label} — {safe_text or 'без текста'}")
                else:
                    lines.append(safe_text or "без текста")
        else:
            lines.append("нет сообщений в истории")

        keyboard = InlineKeyboardBuilder()
        keyboard.button(
            text="▶️ Продолжить диалог",
            callback_data=f"manual_dialog:start:{user.id}",
        )
        keyboard.adjust(1)

        try:
            await self.bot.send_message(
                chat_id=self.manager_channel_id,
                text="\n".join(lines),
                parse_mode="HTML",
                reply_markup=keyboard.as_markup(),
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.error(
                "manager_request_notify_failed",
                user_id=user.id,
                error=str(exc),
            )

    async def notify_consultation_confirmed(self, appointment: Appointment):
        """Notify managers that a consultation is confirmed."""
        user = await self._get_user_info(appointment.user_id)
        message_text = await self._format_message(appointment, user, "✅ Консультация подтверждена")
        # In a real scenario, this would be sent as a reply to the original message
        await self.bot.send_message(self.manager_channel_id, message_text, parse_mode="HTML")

    async def notify_consultation_rescheduled(self, appointment: Appointment):
        """Notify managers that a consultation is rescheduled."""
        user = await self._get_user_info(appointment.user_id)
        message_text = await self._format_message(appointment, user, "📅 Консультация перенесена")
        await self.bot.send_message(self.manager_channel_id, message_text, parse_mode="HTML")

    async def notify_consultation_cancelled(self, appointment: Appointment):
        """Notify managers that a consultation is cancelled."""
        user = await self._get_user_info(appointment.user_id)
        message_text = await self._format_message(appointment, user, "❌ Консультация отменена")
        await self.bot.send_message(self.manager_channel_id, message_text, parse_mode="HTML")

    async def notify_new_application(
        self,
        user: User,
        name: str,
        phone: str,
        telegram_html: str,
        email: str,
        survey_lines: Sequence[str],
        status: str,
        lead_id: Optional[int],
    ):
        """Notify managers about a new application."""
        if not self.manager_channel_id:
            self.logger.warning("Manager channel ID is not configured. Skipping notification.")
            return

        segment_label, lead_score_value = await self._resolve_segment_and_score(user)

        message_text = self._build_application_card(
            title="Новая заявка!",
            name=name,
            phone_display=phone,
            telegram_html=telegram_html,
            email=email or "не указан",
            survey_lines=list(survey_lines),
            status_text=status,
            segment=segment_label,
            lead_score=lead_score_value,
        )

        keyboard = self._build_take_button_markup(lead_id, user.id)

        message = None
        try:
            message = await self.bot.send_message(
                chat_id=self.manager_channel_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception as e:
            self.logger.error("Failed to send new application notification", error=e)
        else:
            if lead_obj and message and script_service:
                await script_service.log_lead_card_posted(
                    lead_obj.id,
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                )

    async def _collect_survey_pairs(self, user_id: int) -> List[Tuple[str, str]]:
        """Return survey answers as (label, value) pairs ordered for the card."""
        service = SurveyService(self.session)
        answers = await service.repository.get_user_answers(user_id)
        answer_map = {answer.question_code: answer.answer_code for answer in answers}

        ordered_codes = ("q1", "q2", "q3", "q4", "q5")
        result: List[Tuple[str, str]] = []

        for code in ordered_codes:
            answer_code = answer_map.get(code)
            if not answer_code:
                continue
            question = service.questions.get(code)
            if not question:
                continue
            option = question["options"].get(answer_code)
            if not option:
                continue
            label = _QUESTION_LABELS.get(code, question["text"])
            result.append((label, option["text"]))

        return result

    async def _resolve_segment_and_score(self, user: User) -> tuple[str, int]:
        """Ensure user segment/score are populated and return normalized values."""
        score = user.lead_score or 0
        segment_value = getattr(user.segment, "value", user.segment)
        updated = False

        if score <= 0:
            survey_service = SurveyService(self.session)
            score = await survey_service.repository.calculate_total_score(user.id)
            if score:
                user.lead_score = score
                updated = True

        if segment_value:
            segment_label = self._segment_label(segment_value)
        else:
            segment_label = "не указан"
            if score > 0:
                user_service = UserService(self.session)
                try:
                    segment_enum = await user_service.calculate_segment_from_score(score)
                    segment_value = segment_enum.value
                    user.segment = segment_value
                    segment_label = self._segment_label(segment_value)
                    updated = True
                except Exception as exc:  # pragma: no cover - defensive logging
                    self.logger.warning(
                        "Failed to derive segment from score",
                        user_id=user.id,
                        score=score,
                        error=str(exc),
                    )
        if updated:
            await self.session.flush()

        return segment_label, score or 0

    def _build_status_text(self, user: User, consultation_dt: Optional[datetime]) -> str:
        """Create a human-readable status for the card."""
        if consultation_dt:
            return f"назначена консультация ({consultation_dt.strftime('%d.%m.%Y %H:%M')} МСК)"
        if isinstance(user.funnel_stage, FunnelStage):
            stage = user.funnel_stage
        elif user.funnel_stage:
            try:
                stage = FunnelStage(user.funnel_stage)
            except ValueError:
                stage = None
        else:
            stage = None

        if stage:
            label = _STAGE_LABELS.get(stage)
            if label:
                return label
        return "на связи с ботом"

    def _segment_label(self, segment: Optional[str]) -> str:
        """Return normalized segment label."""
        raw = getattr(segment, "value", segment)  # handle Enum or plain string
        if not raw:
            return "не указан"
        key = str(raw).lower()
        return _SEGMENT_LABELS.get(key, raw)

    def _format_phone_display(self, phone: Optional[str]) -> str:
        """Normalize phone number for human-friendly display."""
        if not phone:
            return "не указан"

        digits = re.sub(r"\D", "", phone)
        if not digits:
            return phone

        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        if len(digits) == 10 and digits.startswith("9"):
            digits = "7" + digits

        display = f"+{digits}"
        if len(digits) == 11 and digits.startswith("7"):
            display = f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:]}"

        return display

    def _build_telegram_html(self, user: User) -> str:
        """Build telegram link suitable for HTML card."""
        if user.username:
            return html.escape(f"@{user.username}")
        return f'<a href="tg://user?id={user.telegram_id}">профиль</a>'

    def _build_application_card(
        self,
        *,
        title: str,
        name: str,
        phone_display: str,
        telegram_html: str,
        email: str,
        survey_lines: Sequence[str],
        status_text: str,
        segment: str,
        lead_score: int,
        consultation_dt: Optional[datetime] = None,
    ) -> str:
        """Render the manager-facing card in HTML format."""
        lines = [
            f"<b>{html.escape(title)}</b>",
            "",
            f"Имя: {html.escape(name)}",
            f"Телефон: {html.escape(phone_display)}",
            f"Telegram: {telegram_html}",
            f"Email: {html.escape(email)}",
        ]

        if consultation_dt:
            lines.append(f"Консультация: {consultation_dt.strftime('%d.%m.%Y %H:%M')} МСК")

        lines.extend(
            [
                f"Сегмент: {html.escape(segment or 'не указан')}",
                f"Баллы: {lead_score}",
                "",
                "<b>Ответы анкеты:</b>",
            ]
        )

        if survey_lines:
            lines.extend(survey_lines)
        else:
            lines.append("не указано")

        lines.extend(
            [
                "",
                f"Статус: {html.escape(status_text)}",
            ]
        )

        return "\n".join(lines)

    def _build_lead_summary(
        self,
        *,
        name: str,
        phone_display: str,
        email: str,
        survey_data: Sequence[Tuple[str, str]],
        status_text: str,
        segment: str,
        lead_score: int,
        consultation_dt: Optional[datetime],
    ) -> str:
        """Prepare plain-text summary for lead creation."""
        parts = [
            f"Имя: {name or 'не указано'}",
            f"Телефон: {phone_display or 'не указан'}",
            f"Email: {email or 'не указан'}",
            f"Сегмент: {segment or 'не указан'}",
            f"Баллы: {lead_score}",
        ]

        if consultation_dt:
            parts.append(f"Консультация: {consultation_dt.strftime('%d.%m.%Y %H:%M')} МСК")

        if survey_data:
            parts.append("Ответы анкеты:")
            parts.extend(f"- {label}: {answer}" for label, answer in survey_data)
        else:
            parts.append("Ответы анкеты: не указано")

        parts.append(f"Статус: {status_text}")
        return "\n".join(parts)

    def _shorten(self, text: str, limit: int = 300) -> str:
        """Trim long message fragments for manager context."""
        if len(text) <= limit:
            return text
        return text[: max(limit - 3, 0)].rstrip() + "..."

    async def _ensure_active_lead(self, user: User, summary: str, *, trigger: str) -> Optional[int]:
        """Ensure there is an assignable lead for the user and return its ID."""
        lead_service = LeadService(self.session)
        leads = await lead_service.repository.get_user_leads(user.id)

        for lead in leads:
            if isinstance(lead.status, LeadStatus):
                status_value = lead.status
            else:
                try:
                    status_value = LeadStatus(lead.status)
                except ValueError:
                    continue
            if status_value in {LeadStatus.NEW, LeadStatus.INCOMPLETE}:
                return lead.id

        try:
            lead = await lead_service.create_lead(
                user_id=user.id,
                summary=summary,
                trigger=trigger,
            )
            return lead.id
        except Exception as exc:  # pragma: no cover - defensive logging
            self.logger.error(
                "Failed to create lead for consultation notification",
                error=str(exc),
                user_id=user.id,
            )
            return leads[0].id if leads else None

    def _build_lead_channel_keyboard(self, lead_id: int, user_id: int) -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()

        if settings.sales_script_enabled:
            builder.button(
                text="🧾 Скрипт",
                callback_data=f"{Callbacks.LEAD_SCRIPT_SHOW}:{lead_id}",
            )

        builder.button(
            text="✅ Взять заявку",
            callback_data=f"lead:take:{lead_id}",
        )
        builder.button(
            text="✉️ Открыть диалог",
            callback_data=f"manual_dialog:start:{user_id}",
        )
        builder.button(
            text="🔁 Перенести/Отменить",
            callback_data=f"{Callbacks.CONSULT_RESCHEDULE}:{user_id}",
        )
        builder.adjust(1)
        return builder.as_markup()

    def _build_take_button_markup(self, lead_id: Optional[int], user_id: int) -> Optional[InlineKeyboardMarkup]:
        """Create inline keyboard for application/consultation notifications."""
        if not lead_id:
            return None

        builder = InlineKeyboardBuilder()

        if settings.sales_script_enabled:
            builder.button(
                text="🧾 Скрипт",
                callback_data=f"{Callbacks.LEAD_SCRIPT_SHOW}:{lead_id}",
            )

        builder.button(
            text="✅ Взять заявку",
            callback_data=f"{Callbacks.APPLICATION_TAKE}:{lead_id}:{user_id}",
        )
        builder.button(
            text="✉️ Открыть диалог",
            callback_data=f"manual_dialog:start:{user_id}",
        )
        builder.button(
            text="🔁 Перенести/Отменить",
            callback_data=f"{Callbacks.CONSULT_RESCHEDULE}:{user_id}",
        )
        builder.adjust(1)
        return builder.as_markup()
