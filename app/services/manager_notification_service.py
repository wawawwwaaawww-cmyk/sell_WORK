"""Service for sending notifications to managers."""

import structlog
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Appointment, User


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

    async def _format_message(self, appointment: Appointment, user: User, title: str) -> str:
        """Format the notification message."""
        user_info = f"Пользователь: {user.first_name or ''} {user.last_name or ''}".strip()
        if user.username:
            user_info += f" (@{user.username})"
        
        phone_info = f"Телефон: {user.phone}" if user.phone else "Телефон: не указан"
        
        slot_msk = f"{appointment.date.strftime('%d.%m.%Y')} в {appointment.slot.strftime('%H:%M')} МСК"

        return (
            f"<b>{title}</b>\n\n"
            f"🗓 {slot_msk}\n"
            f"👤 {user_info}\n"
            f"📞 {phone_info}\n"
            f"📊 Сегмент: {user.segment or 'N/A'}\n"
            f"📈 Баллы: {user.lead_score or 0}"
        )

    async def notify_new_consultation(self, appointment: Appointment):
        """Notify managers about a new consultation."""
        if not self.manager_channel_id:
            self.logger.warning("Manager channel ID is not configured. Skipping notification.")
            return

        user = await self._get_user_info(appointment.user_id)
        if not user:
            return

        message_text = await self._format_message(appointment, user, " новая консультация")
        
        try:
            await self.bot.send_message(
                chat_id=self.manager_channel_id,
                text=message_text,
                parse_mode="HTML"
            )
        except Exception as e:
            self.logger.error("Failed to send new consultation notification", error=e)

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
