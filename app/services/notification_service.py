"""Notification service for sending automated messages."""

import logging
from datetime import datetime
from typing import List, Tuple

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from ..config import settings
from ..models import Lead

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending notifications to users and managers."""
    
    def __init__(self):
        self.bot = Bot(token=settings.telegram_bot_token)
    
    async def send_lead_reminder(self, manager_id: int, leads: List[Tuple]):
        """Send daily lead reminder to manager."""
        try:
            if not leads:
                return
            
            text = "🔔 <b>Ежедневное напоминание о лидах</b>\n\n"
            text += f"У вас есть {len(leads)} необработанных лидов:\n\n"
            
            for lead, telegram_id, full_name in leads[:5]:  # Show max 5
                text += f"• {full_name or 'N/A'} (@{telegram_id})\n"
                text += f"  Создан: {lead.created_at.strftime('%d.%m.%Y %H:%M')}\n\n"
            
            if len(leads) > 5:
                text += f"... и еще {len(leads) - 5} лидов\n\n"
            
            text += "💼 Пожалуйста, свяжитесь с ними как можно скорее!"
            
            await self.bot.send_message(
                chat_id=manager_id,
                text=text,
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"Error sending lead reminder to manager {manager_id}: {e}")
    
    async def send_consultation_reminder(self, user_id: int, consultation_time: datetime):
        """Send consultation reminder to user."""
        try:
            time_str = consultation_time.strftime("%d.%m.%Y в %H:%M")
            
            text = "⏰ <b>Напоминание о консультации</b>\n\n"
            text += f"Ваша консультация назначена на {time_str}\n\n"
            text += "📞 Наши менеджеры свяжутся с вами в указанное время.\n\n"
            text += "❓ Если у вас есть вопросы или нужно изменить время, "
            text += "пожалуйста, свяжитесь с нами заранее."
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📞 Связаться с поддержкой",
                    callback_data="contact_support"
                )]
            ])
            
            await self.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"Error sending consultation reminder to user {user_id}: {e}")
    
    async def send_reengagement_message(self, user_id: int, segment: str):
        """Send re-engagement message to inactive user."""
        try:
            messages = {
                "warm": {
                    "text": "👋 Привет! Мы заметили, что вы давно не заходили к нам.\n\n"
                            "🎯 У нас есть новые материалы по криптовалютам, которые могут вас заинтересовать!\n\n"
                            "💡 Хотите узнать о последних трендах в мире крипто?",
                    "button": "📚 Посмотреть новые материалы"
                },
                "hot": {
                    "text": "🔥 Привет! Вы проявляли активный интерес к нашим материалам.\n\n"
                            "💰 У нас есть эксклюзивное предложение именно для вас!\n\n"
                            "⚡ Не упустите возможность получить персональную консультацию со скидкой.",
                    "button": "🎯 Узнать о предложении"
                }
            }
            
            message_data = messages.get(segment, messages["warm"])
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text=message_data["button"],
                    callback_data="reengagement_action"
                )]
            ])
            
            await self.bot.send_message(
                chat_id=user_id,
                text=message_data["text"],
                reply_markup=keyboard
            )
            
        except Exception as e:
            logger.error(f"Error sending reengagement message to user {user_id}: {e}")
    
    async def send_lead_followup(self, manager_id: int, lead: Lead, telegram_id: int, full_name: str):
        """Send lead follow-up notification to manager."""
        try:
            text = "📋 <b>Напоминание о лиде</b>\n\n"
            text += f"👤 Лид: {full_name or 'N/A'} (@{telegram_id})\n"
            text += f"📅 Создан: {lead.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            text += f"⏰ Статус: {lead.status}\n\n"
            text += "⚠️ Этот лид требует дополнительного внимания. "
            text += "Пожалуйста, свяжитесь с клиентом."
            
            await self.bot.send_message(
                chat_id=manager_id,
                text=text,
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"Error sending lead follow-up to manager {manager_id}: {e}")
    
    async def send_payment_notification(self, manager_id: int, user_id: int, amount: float, course_name: str):
        """Send payment notification to manager."""
        try:
            text = "💰 <b>Новый платеж!</b>\n\n"
            text += f"👤 Пользователь: @{user_id}\n"
            text += f"💵 Сумма: {amount:,.2f} ₽\n"
            text += f"📚 Курс: {course_name}\n"
            text += f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
            text += "✅ Платеж успешно обработан!"
            
            await self.bot.send_message(
                chat_id=manager_id,
                text=text,
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"Error sending payment notification: {e}")