"""Service to notify admins about spam-related events."""

from datetime import datetime
import pytz
import structlog
from aiogram.types import User, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import Bot

from app.config import settings

logger = structlog.get_logger(__name__)

async def send_ban_notification(bot: Bot, user: User, ban_details: dict, stats: dict) -> None:
    """
    Sends a formatted notification to the admin channel about a user ban.
    """
    if not settings.dialogs_channel_id:
        logger.warning("dialogs_channel_id is not set. Cannot send ban notification.")
        return

    user_mention = f"@{user.username}" if user.username else f"ID: {user.id}"
    banned_until_str = datetime.fromisoformat(ban_details['banned_until']).astimezone(pytz.timezone('Europe/Moscow')).strftime('%Y-%m-%d %H:%M:%S %Z')

    text = (
        f"🚨 **Обнаружен спам и пользователь заблокирован**\n\n"
        f"👤 **Пользователь:** {user.full_name} ({user_mention})\n"
        f"🆔 **User ID:** `{user.id}`\n\n"
        f"📉 **Причина:** `{ban_details['reason']}`\n"
        f"📊 **Статистика:**\n"
        f"  - Сообщений за 10с: `{stats.get('burst10', 'N/A')}`\n"
        f"  - Сообщений за 60с: `{stats.get('minute60', 'N/A')}`\n"
        f"  - Дубликатов за 30с: `{stats.get('dupes', 'N/A')}`\n\n"
        f"⏳ **Срок бана:** до {banned_until_str}\n"
        f"📈 **Текущая ступень:** `{ban_details['ban_level']}`"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Разбанить сейчас", callback_data=f"spam:unban:{user.id}"),
            InlineKeyboardButton(text="📉 Сбросить уровень", callback_data=f"spam:reset_level:{user.id}"),
        ],
        [
            InlineKeyboardButton(text="⚪️ В белый список", callback_data=f"spam:whitelist:add:{user.id}"),
            InlineKeyboardButton(text="⚫️ Убрать из БС", callback_data=f"spam:whitelist:remove:{user.id}"),
        ]
    ])

    try:
        await bot.send_message(
            chat_id=settings.dialogs_channel_id,
            text=text,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        logger.info("Sent ban notification to admin channel", user_id=user.id)
    except Exception as e:
        logger.error(
            "Failed to send ban notification to admin channel",
            user_id=user.id,
            channel_id=settings.dialogs_channel_id,
            error=str(e),
            exc_info=True
        )