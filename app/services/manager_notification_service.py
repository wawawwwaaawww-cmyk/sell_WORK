"""Manager notification service."""

from typing import Optional, Dict, Any
import asyncio
import html

import structlog
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import Lead, User
from app.services.lead_service import LeadService
from app.config import settings
from app.utils.callbacks import Callbacks


class ManagerNotificationService:
    """Service for sending notifications to managers."""
    
    def __init__(self, bot: Bot, session):
        self.bot = bot
        self.session = session
        self.logger = structlog.get_logger()
        self.manager_channel_id = settings.manager_channel_id
    
    async def notify_new_lead(self, lead: Lead, user: User) -> bool:
        """Send new lead notification to manager channel."""
        if not self.manager_channel_id:
            self.logger.error("Manager channel ID is not configured; cannot publish lead", lead_id=lead.id)
            return False

        try:
            lead_service = LeadService(self.session)
            lead_card = await lead_service.format_lead_card(lead, user)

            priority_display = lead.priority if getattr(lead, 'priority', None) is not None else 40
            lead_card = f"{lead_card}\n\n⭐️ **Приоритет:** {priority_display}"

            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(
                text='✅ Взять заявку',
                callback_data=f'lead:take:{lead.id}'
            ))
            keyboard.add(InlineKeyboardButton(
                text='👁 Профиль',
                callback_data=f'lead:profile:{user.id}'
            ))
            keyboard.add(InlineKeyboardButton(
                text='💬 Перехватить диалог',
                callback_data=f'manager:takeover:{user.id}'
            ))
            keyboard.add(InlineKeyboardButton(
                text='📜 История диалога',
                callback_data=f'user:events:{user.id}'
            ))
            keyboard.adjust(1)

            message = await self.bot.send_message(
                chat_id=self.manager_channel_id,
                text=lead_card,
                reply_markup=keyboard.as_markup(),
                parse_mode='Markdown'
            )

            self.logger.info(
                'Lead notification sent',
                lead_id=lead.id,
                user_id=user.id,
                message_id=message.message_id,
                priority=priority_display,
            )

            return True

        except Exception as e:
            self.logger.error(
                'Error sending lead notification',
                error=str(e),
                lead_id=lead.id,
                exc_info=True,
            )
            return False    

    async def notify_new_application(
        self,
        *,
        user: User,
        name: str,
        phone: str,
        telegram_html: str,
        email: Optional[str],
        survey_lines: list[str],
        status: str,
        lead_id: Optional[int] = None,
    ) -> bool:
        """Send formatted application notification to the manager channel."""
        if not self.manager_channel_id:
            self.logger.error("Manager channel ID is not configured; cannot publish application", user_id=user.id)
            return False

        try:
            name_html = html.escape(name)
            phone_html = html.escape(phone)
            email_html = html.escape(email) if email else "не указана"
            status_html = html.escape(status)

            lines = ["🚨 <b>Новая заявка от клиента!</b>"]
            if lead_id:
                lines.append(f"🆔 <b>ID заявки:</b> {lead_id}")

            lines.extend([
                "",
                f"👤 <b>Имя:</b> {name_html}",
                f"📱 <b>Телефон:</b> {phone_html}",
                f"💬 <b>Telegram:</b> {telegram_html}",
                f"📧 <b>Почта:</b> {email_html}",
            ])

            if survey_lines:
                lines.append("")
                lines.append("📝 <b>Ответы анкеты:</b>")
                lines.extend(f"• {line}" for line in survey_lines)

            lines.extend([
                "",
                f"🔥 <b>Статус:</b> {status_html}",
            ])

            message_text = "\n".join(lines)

            keyboard = None
            if lead_id:
                keyboard = InlineKeyboardBuilder()
                keyboard.add(InlineKeyboardButton(
                    text="✅ Взять заявку",
                    callback_data=f"{Callbacks.APPLICATION_TAKE}:{lead_id}:{user.id}",
                ))
                keyboard.adjust(1)

            await self.bot.send_message(
                chat_id=self.manager_channel_id,
                text=message_text,
                parse_mode="HTML",
                reply_markup=keyboard.as_markup() if keyboard else None,
            )

            self.logger.info(
                "Application notification sent",
                user_id=user.id,
                lead_id=lead_id,
            )

            return True

        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error(
                "Error sending application notification",
                error=str(exc),
                user_id=user.id,
                exc_info=True,
            )
            return False

    async def notify_lead_taken(self, lead: Lead, user: User, manager_id: int) -> bool:
        """Notify that lead was taken and send details to manager."""
        try:
            lead_service = LeadService(self.session)
            
            # Get detailed lead info for manager
            lead_details = await lead_service.get_manager_lead_details(lead, user)
            
            # Send details to manager's DM
            await self.bot.send_message(
                chat_id=manager_id,
                text=f"🎯 **Лид #{lead.id} назначен на вас**\\n\\n{lead_details}",
                parse_mode="Markdown"
            )
            
            # Send confirmation to channel
            await self.bot.send_message(
                chat_id=self.manager_channel_id,
                text=f"✅ **Лид #{lead.id} взят в работу**\\n\\nМенеджер: [ID {manager_id}]",
                parse_mode="Markdown"
            )
            
            self.logger.info(
                "Lead taken notification sent",
                lead_id=lead.id,
                manager_id=manager_id
            )
            
            return True
            
        except Exception as e:
            self.logger.error(
                "Error sending lead taken notification",
                error=str(e),
                lead_id=lead.id,
                exc_info=True
            )
            return False
    
    async def notify_consultation_booked(self, user: User, appointment_date: str, appointment_time: str) -> bool:
        """Notify managers about consultation booking."""
        try:
            notification_text = f"""📅 **Новая консультация**
            
👤 **Клиент:** {user.first_name or ''} {user.last_name or ''}
📱 **Telegram:** @{user.username or 'не указан'}
📞 **Телефон:** {user.phone or 'не указан'}
📧 **Email:** {user.email or 'не указан'}
🎯 **Сегмент:** {user.segment or 'не определен'} ({user.lead_score} баллов)

📅 **Дата:** {appointment_date}
⏰ **Время:** {appointment_time} МСК

🔔 Не забудьте подготовиться к встрече!"""
            
            await self.bot.send_message(
                chat_id=self.manager_channel_id,
                text=notification_text,
                parse_mode="Markdown"
            )
            
            return True
            
        except Exception as e:
            self.logger.error(
                "Error sending consultation notification",
                error=str(e),
                user_id=user.id,
                exc_info=True
            )
            return False
    
    async def notify_payment_initiated(self, user: User, product_name: str, amount: float) -> bool:
        """Notify managers about payment initiation."""
        try:
            notification_text = f"""💳 **Инициирован платеж**
            
👤 **Клиент:** {user.first_name or ''} {user.last_name or ''}
📱 **Telegram:** @{user.username or 'не указан'}
📞 **Телефон:** {user.phone or 'не указан'}

💰 **Продукт:** {product_name}
💵 **Сумма:** {amount:,.0f} руб.

🎯 Проследите за завершением оплаты!"""
            
            await self.bot.send_message(
                chat_id=self.manager_channel_id,
                text=notification_text,
                parse_mode="Markdown"
            )
            
            return True
            
        except Exception as e:
            self.logger.error(
                "Error sending payment notification",
                error=str(e),
                user_id=user.id,
                exc_info=True
            )
            return False
    
    async def notify_manager_request(self, user: User, message: Optional[str] = None) -> bool:
        """Notify about direct manager contact request."""
        try:
            notification_text = f"""👤 **Запрос связи с менеджером**
            
👤 **Клиент:** {user.first_name or ''} {user.last_name or ''} 
📱 **Telegram:** @{user.username or 'не указан'}
📞 **Телефон:** {user.phone or 'не указан'}
🎯 **Сегмент:** {user.segment or 'не определен'} ({user.lead_score} баллов)
📊 **Этап:** {user.funnel_stage}"""
            
            if message:
                notification_text += f"\\n\\n💬 **Сообщение:** {message}"
            
            notification_text += "\\n\\n⚡ Требуется быстрая реакция!"
            
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(
                text="📞 Связаться с клиентом",
                callback_data=f"manager:contact:{user.id}"
            ))
            keyboard.add(InlineKeyboardButton(
                text="💬 Перехватить диалог",
                callback_data=f"manager:takeover:{user.id}"
            ))
            keyboard.adjust(1)
            
            await self.bot.send_message(
                chat_id=self.manager_channel_id,
                text=notification_text,
                reply_markup=keyboard.as_markup(),
                parse_mode="Markdown"
            )
            
            return True
            
        except Exception as e:
            self.logger.error(
                "Error sending manager request notification",
                error=str(e),
                user_id=user.id,
                exc_info=True
            )
            return False
    
    async def send_daily_summary(self) -> bool:
        """Send daily summary to managers."""
        try:
            lead_service = LeadService(self.session)
            stats = await lead_service.get_lead_statistics()
            
            summary_text = f"""📊 **Ежедневная сводка**
            
🎯 **Лиды:**
• Всего активных: {stats.get('total_active', 0)}
• Новых: {stats.get('new_leads', 0)}
• В работе: {stats.get('taken_leads', 0)}
• За сегодня: {stats.get('leads_today', 0)}

📈 **Рекомендации:**
• Проверьте новые лиды
• Свяжитесь с клиентами в течение 15 минут
• Обновите статусы завершенных лидов

Удачного дня! 🚀"""
            
            await self.bot.send_message(
                chat_id=self.manager_channel_id,
                text=summary_text,
                parse_mode="Markdown"
            )
            
            return True
            
        except Exception as e:
            self.logger.error(
                "Error sending daily summary",
                error=str(e),
                exc_info=True
            )
            return False
    
    async def notify_high_value_interaction(
        self,
        user: User,
        interaction_type: str,
        details: Optional[str] = None
    ) -> bool:
        """Notify about high-value user interactions."""
        try:
            interaction_names = {
                "survey_completed_hot": "🔥 Завершил анкету с высоким баллом",
                "multiple_material_requests": "📚 Запросил много материалов",
                "repeated_consultation_attempts": "📅 Неоднократно пытался записаться",
                "payment_page_visited": "💳 Посетил страницу оплаты",
                "high_engagement_score": "⚡ Высокая активность в боте"
            }
            
            interaction_name = interaction_names.get(interaction_type, interaction_type)
            
            notification_text = f"""🎯 **Высокоценное взаимодействие**
            
{interaction_name}

👤 **Клиент:** {user.first_name or ''} {user.last_name or ''}
📱 **Telegram:** @{user.username or 'не указан'} 
🎯 **Сегмент:** {user.segment or 'не определен'} ({user.lead_score} баллов)"""
            
            if details:
                notification_text += f"\\n\\n📝 **Детали:** {details}"
            
            notification_text += "\\n\\n💡 Рекомендуется связаться в ближайшее время!"
            
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(
                text="📞 Связаться",
                callback_data=f"manager:contact:{user.id}"
            ))
            keyboard.add(InlineKeyboardButton(
                text="🎯 Создать лид",
                callback_data=f"lead:create:{user.id}"
            ))
            keyboard.adjust(1)
            
            await self.bot.send_message(
                chat_id=self.manager_channel_id,
                text=notification_text,
                reply_markup=keyboard.as_markup(),
                parse_mode="Markdown"
            )
            
            return True
            
        except Exception as e:
            self.logger.error(
                "Error sending high-value interaction notification",
                error=str(e),
                user_id=user.id,
                exc_info=True
            )
            return False
