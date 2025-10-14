"""Handlers for admin actions related to anti-spam."""

import json
import structlog
from aiogram import Router, F
from aiogram.types import CallbackQuery

from app.config import settings
from app.services.redis_service import redis_service
from app.logging_spam import spam_events_logger

logger = structlog.get_logger(__name__)
router = Router()

async def is_admin(callback_query: CallbackQuery) -> bool:
    """Check if the user is an admin."""
    return callback_query.from_user.id in settings.admin_ids_list

@router.callback_query(F.data.startswith("spam:"), is_admin)
async def handle_spam_action(callback_query: CallbackQuery):
    """Handles all spam-related admin actions."""
    action_parts = callback_query.data.split(":")
    action = action_parts[1]
    target_user_id = int(action_parts[2])
    
    redis = redis_service.get_client()
    if not redis:
        await callback_query.answer("Ошибка: Redis недоступен.", show_alert=True)
        return

    ban_key = f"ban:{target_user_id}"
    
    if action == "unban":
        await redis.delete(ban_key)
        await callback_query.answer(f"Пользователь {target_user_id} разбанен.", show_alert=True)
        logger.info("Admin unbanned user", admin_id=callback_query.from_user.id, target_user_id=target_user_id)
        spam_events_logger.info(json.dumps({
            "admin_id": callback_query.from_user.id,
            "user_id": target_user_id,
            "action": "unban"
        }))
        # Optionally, edit the original message to reflect the action
        await callback_query.message.edit_text(callback_query.message.text + "\n\n✅ Пользователь разбанен.", parse_mode="Markdown")

    elif action == "reset_level":
        ban_data_raw = await redis.get(ban_key)
        if ban_data_raw:
            ban_data = json.loads(ban_data_raw)
            ban_data["ban_level"] = 0
            ttl = await redis.ttl(ban_key)
            await redis.set(ban_key, json.dumps(ban_data), ex=ttl if ttl > 0 else None)
        await callback_query.answer(f"Уровень бана для пользователя {target_user_id} сброшен.", show_alert=True)
        logger.info("Admin reset ban level", admin_id=callback_query.from_user.id, target_user_id=target_user_id)
        spam_events_logger.info(json.dumps({
            "admin_id": callback_query.from_user.id,
            "user_id": target_user_id,
            "action": "reset_level"
        }))
        await callback_query.message.edit_text(callback_query.message.text + "\n\n📉 Уровень бана сброшен.", parse_mode="Markdown")

    elif action == "whitelist":
        sub_action = action_parts[2]
        user_id_to_modify = int(action_parts[3])
        # This is a simplified whitelist stored in Redis. For production, consider a persistent storage.
        whitelist_key = "spam:whitelist"
        if sub_action == "add":
            await redis.sadd(whitelist_key, user_id_to_modify)
            await callback_query.answer(f"Пользователь {user_id_to_modify} добавлен в белый список.", show_alert=True)
            logger.info("Admin added user to whitelist", admin_id=callback_query.from_user.id, target_user_id=user_id_to_modify)
            spam_events_logger.info(json.dumps({
                "admin_id": callback_query.from_user.id,
                "user_id": user_id_to_modify,
                "action": "whitelist_add"
            }))
            await callback_query.message.edit_text(callback_query.message.text + f"\n\n⚪️ Пользователь {user_id_to_modify} добавлен в белый список.", parse_mode="Markdown")
        elif sub_action == "remove":
            await redis.srem(whitelist_key, user_id_to_modify)
            await callback_query.answer(f"Пользователь {user_id_to_modify} удален из белого списка.", show_alert=True)
            logger.info("Admin removed user from whitelist", admin_id=callback_query.from_user.id, target_user_id=user_id_to_modify)
            spam_events_logger.info(json.dumps({
                "admin_id": callback_query.from_user.id,
                "user_id": user_id_to_modify,
                "action": "whitelist_remove"
            }))
            await callback_query.message.edit_text(callback_query.message.text + f"\n\n⚫️ Пользователь {user_id_to_modify} удален из белого списка.", parse_mode="Markdown")

    else:
        await callback_query.answer("Неизвестное действие.")