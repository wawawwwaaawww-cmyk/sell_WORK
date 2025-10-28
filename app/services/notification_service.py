"""Notification service for sending automated messages."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import Lead, User, LeadStatus
from ..utils.callbacks import Callbacks
from .sales_script_service import SalesScriptService

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

    async def send_ab_test_summary(self, manager_id: int, summary: Dict[str, Any]):
        """Send A/B test summary message to initiator."""
        try:
            test_name = summary.get("name") or "A/B тест"
            variants = summary.get("variants") or []
            started_at = summary.get("started_at")
            audience_size = summary.get("audience_size") or 0
            test_size = summary.get("test_size") or 0
            variants_count = len(variants)

            started_dt: Optional[datetime] = None
            if isinstance(started_at, datetime):
                started_dt = started_at
            elif isinstance(started_at, str):
                try:
                    started_dt = datetime.fromisoformat(started_at)
                except ValueError:
                    started_dt = None

            started_str = started_dt.astimezone().strftime("%d.%m.%Y %H:%M") if started_dt else "неизвестно"
            coverage_pct = (test_size / audience_size) * 100 if audience_size else 0.0

            lines = [
                f"🧪 <b>Итоги A/B теста «{test_name}»</b>",
                f"Старт: {started_str}",
                f"Охват: {test_size} из {audience_size} ({coverage_pct:.1f}% аудитории)",
                f"Вариантов: {variants_count}",
            ]

            if not variants or all(item.get("delivered", 0) == 0 for item in variants):
                lines.append("")
                lines.append("⚠️ Сообщения не были доставлены — данных недостаточно.")
            else:
                lines.append("")
                for variant in variants:
                    delivered = variant.get("delivered", 0)
                    clicks = variant.get("unique_clicks", 0)
                    leads = variant.get("leads", 0)
                    unsubscribed = variant.get("unsubscribed", 0)
                    blocked = variant.get("blocked", 0)
                    ctr_pct = (variant.get("ctr", 0.0) or 0.0) * 100
                    cr_pct = (variant.get("cr", 0.0) or 0.0) * 100
                    unsub_rate_pct = (variant.get("unsub_rate", 0.0) or 0.0) * 100

                    lines.append(
                        f"• Вариант {variant.get('variant')}: доставлено {delivered}, "
                        f"клики {clicks}, CTR {ctr_pct:.1f}%, лиды {leads}, "
                        f"CR {cr_pct:.1f}%, отписки {unsubscribed} ({unsub_rate_pct:.1f}%), "
                        f"блокировки {blocked}"
                    )

            winner = summary.get("winner")
            lines.append("")
            if winner:
                winner_ctr = (winner.get("ctr", 0.0) or 0.0) * 100
                winner_cr = (winner.get("cr", 0.0) or 0.0) * 100
                winner_unsub = (winner.get("unsub_rate", 0.0) or 0.0) * 100
                lines.append(
                    f"🏆 Победитель: вариант {winner.get('variant')} "
                    f"(CTR {winner_ctr:.1f}%, CR {winner_cr:.1f}%, отписки {winner_unsub:.1f}%)"
                )
            else:
                lines.append("🏳️ Победитель не определён.")

            await self.bot.send_message(
                chat_id=manager_id,
                text="\n".join(lines),
                parse_mode="HTML",
            )

        except Exception as exc:
            logger.error("Error sending A/B test summary", exc_info=exc)
    
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

    async def send_incomplete_lead_to_managers(
        self,
        session: AsyncSession,
        lead: Lead,
        user: User,
        card_text: str,
    ):
        """Send notification about an incomplete lead to the admin channel."""
        channel_id = settings.incomplete_leads_admin_channel_id
        if not channel_id:
            logger.warning("Incomplete leads channel is not configured.")
            return

        script_service: Optional[SalesScriptService] = None
        if settings.sales_script_enabled:
            script_service = SalesScriptService(session, self.bot)
            try:
                await script_service.ensure_script(
                    lead,
                    user,
                    reason="incomplete_lead_card",
                )
            except Exception as exc:  # pragma: no cover
                logger.warning(
                    "sales_script_prepare_failed",
                    lead_id=lead.id,
                    error=str(exc),
                )

        keyboard_rows = []
        if settings.sales_script_enabled:
            keyboard_rows.append(
                [InlineKeyboardButton(text="🧾 Скрипт", callback_data=f"{Callbacks.LEAD_SCRIPT_SHOW}:{lead.id}")]
            )
        keyboard_rows.append(
            [InlineKeyboardButton(text="✅ Взять заявку", callback_data=f"lead:take:{lead.id}")]
        )
        keyboard_rows.append(
            [InlineKeyboardButton(text="✉️ Открыть диалог", callback_data=f"manual_dialog:start:{user.id}")]
        )
        keyboard_rows.append(
            [InlineKeyboardButton(text="🔁 Перенести/Отменить", callback_data=f"{Callbacks.CONSULT_RESCHEDULE}:{user.id}")]
        )

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

        try:
            message = await self.bot.send_message(
                chat_id=channel_id,
                text=card_text,
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            logger.info("Sent incomplete lead notification to managers", lead_id=lead.id)

            if script_service and message:
                await script_service.log_lead_card_posted(
                    lead.id,
                    chat_id=channel_id,
                    message_id=message.message_id,
                )
        except Exception as exc:
            logger.error(
                "Error sending incomplete lead notification for lead %s: %s",
                lead.id,
                exc,
            )
