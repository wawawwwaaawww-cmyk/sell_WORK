
"""Shared helpers for routing commands and callbacks through SceneManager."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

import structlog
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.scenes.base_scene import SceneResponse
from app.scenes.scene_manager import SceneManager
from app.services.event_service import EventService
from app.services.lead_service import LeadService
from app.services.logging_service import ConversationLoggingService
from app.services.manager_notification_service import ManagerNotificationService


logger = structlog.get_logger()

_SCENE_CALLBACK_ALIASES: Dict[str, str] = {
    "consult:after_check": "consult:schedule",
    "consult:expert_vip": "consult:schedule",
    "consult:personal_strategy": "consult:schedule",
    "consult:offer": "consult:schedule",
    "consult:offer:vip": "consult:schedule",
    "consult:offer_payment": "offer:payment",
    "contact:quality": "manager:request",
    "send:documents": "manager:request",
    "strategy:explain": "strategy:discuss",
    "strategy:safety": "strategy:path:safety",
    "strategy:growth": "strategy:path:growth",
    "materials:all_cases": "materials:safety",
    "materials:beginners": "materials:safety",
    "materials:budget": "materials:safety",
    "bonus:get": "bonus:claim",
    "retry": "survey:start",
    "survey:start": "survey:start",
    "survey_start": "survey:start",
    "payment:crypto_elite": "offer:payment",
    "payment:vip_access": "offer:payment",
    "offer:course": "offer:payment",
    "manager:call": "manager:request",
}

_SCENE_TRIGGER_MAP: Dict[str, str] = {
    "survey:start": "callback:survey:start",
    "survey:q1": "callback:survey:start",
    "survey:q1:beginner": "callback:survey_answer",
    "survey:q1:some_exp": "callback:survey_answer",
    "survey:q1:advanced": "callback:survey_answer",
    "strategy:discuss": "callback:strategy:discuss",
    "strategy:path:safety": "callback:strategy:path:safety",
    "strategy:path:growth": "callback:strategy:path:growth",
    "consult:schedule": "callback:consult:schedule",
    "manager:request": "callback:manager:request",
    "offer:payment": "callback:offer:payment",
    "bonus:claim": "callback:bonus:claim",
    "materials:safety": "callback:strategy:path:safety",
    "materials:growth": "callback:strategy:path:growth",
    "materials:category:cases": "callback:strategy:path:growth",
    "materials:category:educational": "callback:strategy:path:safety",
    "products:safety": "callback:consult:schedule",
    "products:growth": "callback:consult:schedule",
}

_ALLOWED_PREFIXES: Sequence[str] = (
    "callback:",
    "cta:",
    "segment:",
    "survey_answer:",
    "payment_status:",
    "objection_",
    "consult_slot",
    "manager_",
    "followup_",
)


def normalize_callback(callback_data: str) -> str:
    """Return canonical callback value used in scene transitions."""
    return _SCENE_CALLBACK_ALIASES.get(callback_data, callback_data)


def resolve_trigger(raw_callback: str) -> Optional[str]:
    """Map raw callback payload to scene trigger string."""
    canonical = normalize_callback(raw_callback)
    if canonical.startswith(tuple(_ALLOWED_PREFIXES)):
        return canonical
    if canonical.startswith("survey:q"):
        return "callback:survey_answer"
    return _SCENE_TRIGGER_MAP.get(canonical)


def _scene_manager(session: AsyncSession) -> Optional[SceneManager]:
    manager = SceneManager(session)
    if not manager.config_enabled:
        return None
    return manager


def _build_keyboard(buttons: Optional[Sequence[Dict[str, str]]]) -> Optional[InlineKeyboardMarkup]:
    if not buttons:
        return None

    builder = InlineKeyboardBuilder()
    has_buttons = False

    for button in buttons:
        text = button.get("text")
        if not text:
            continue

        url = button.get("url")
        callback = button.get("callback_data") or button.get("callback")

        if url:
            builder.add(InlineKeyboardButton(text=text, url=url))
            has_buttons = True
            continue

        if callback:
            builder.add(InlineKeyboardButton(text=text, callback_data=normalize_callback(callback)))
            has_buttons = True

    if not has_buttons:
        return None

    builder.adjust(1)
    return builder.as_markup()


async def send_scene_response(
    message: Message,
    response: SceneResponse,
    *,
    session: AsyncSession,
    user: User,
    use_edit: bool = False,
    default_event: Optional[str] = None,
    default_payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Render scene response to Telegram message and log event if provided."""
    if message is None:
        logger.warning("scene_response_without_message", user_id=user.id)
        return

    markup = _build_keyboard(response.buttons)
    prefer_edit = bool(use_edit)
    metadata: Dict[str, Any] = {}
    if default_payload:
        metadata.update(default_payload)
    if response.log_event:
        metadata.update(response.log_event)

    conversation_logger = ConversationLoggingService(session)

    if response.message_text:
        try:
            await conversation_logger.send_or_edit(
                message,
                text=response.message_text,
                user_id=user.id,
                reply_markup=markup,
                parse_mode="HTML",
                metadata=metadata or None,
                prefer_edit=prefer_edit,
            )
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("scene_response_render_failed", error=str(exc), user_id=user.id, exc_info=True)
            if markup is not None:
                try:
                    await message.answer(response.message_text, reply_markup=markup, parse_mode="HTML")
                except Exception:  # pragma: no cover - secondary failure
                    logger.warning("scene_response_secondary_failure", user_id=user.id)
    elif markup is not None:
        try:
            await message.edit_reply_markup(reply_markup=markup)
        except Exception as exc:  # pylint: disable=broad-except
            logger.debug("scene_markup_update_failed", error=str(exc), user_id=user.id)

    event_payload = response.log_event
    if event_payload is None and default_payload:
        event_payload = dict(default_payload)

    if event_payload is not None:
        event_service = EventService(session)
        event_type = (
            event_payload.get("event")
            or event_payload.get("action")
            or default_event
            or "scene_event"
        )
        await event_service.log_event(
            user_id=user.id,
            event_type=event_type,
            payload=event_payload,
        )

    if response.escalate:
        await escalate_to_manager(message, user, session, event_payload)


async def escalate_to_manager(
    message: Message,
    user: User,
    session: AsyncSession,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """Create lead and notify managers about escalation."""
    lead_service = LeadService(session)
    manager_service = ManagerNotificationService(message.bot, session)

    context_flags = {"manager_requested": True}
    if context and context.get("action") == "payment_initiated":
        context_flags["payment_initiated"] = True

    if await lead_service.should_create_lead(user, context_flags):
        summary = None
        if context:
            summary = (
                f"Эскалация из сценария: {context.get('scene', 'unknown')}"
                if context.get("scene")
                else "Эскалация из сценария"
            )
        else:
            summary = f"Эскалация по запросу пользователя. Сообщение: {message.text[:200] if message.text else ''}"

        lead = await lead_service.create_lead_from_user(
            user=user,
            trigger_event=context.get("action") if context else "scene_escalation",
            conversation_summary=summary,
        )
        await manager_service.notify_new_lead(lead, user)

    await manager_service.notify_manager_request(user)


async def try_process_command(
    message: Message,
    command: str,
    *,
    session: AsyncSession,
    user: User,
    payload: Optional[Dict[str, Any]] = None,
    default_event: Optional[str] = None,
    default_payload: Optional[Dict[str, Any]] = None,
) -> bool:
    """Attempt to handle command via scenario engine.

    Returns True if config handled the command, otherwise False.
    """
    manager = _scene_manager(session)
    if not manager:
        return False

    command_name = command if command.startswith("/") else f"/{command}"
    trigger = f"command:{command_name}"

    trigger_payload = {"source": "command", "command": command_name}
    if payload:
        trigger_payload.update(payload)

    try:
        response = await manager.process_trigger(
            user,
            trigger,
            chat_id=message.chat.id,
            payload=trigger_payload,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "scene_trigger_failed",
            trigger=trigger,
            error=str(exc),
            user_id=user.id,
            exc_info=True,
        )
        return False

    await send_scene_response(
        message,
        response,
        session=session,
        user=user,
        default_event=default_event,
        default_payload=default_payload or {"trigger": trigger},
    )
    return True


async def try_process_callback(
    callback: CallbackQuery,
    *,
    session: AsyncSession,
    user: User,
    trigger: Optional[str] = None,
    extra_payload: Optional[Dict[str, Any]] = None,
    use_edit: bool = True,
) -> bool:
    """Attempt to handle callback via scenario engine."""
    manager = _scene_manager(session)
    if not manager:
        return False

    raw_data = callback.data or ""
    resolved_trigger = trigger or resolve_trigger(raw_data)
    if not resolved_trigger:
        return False

    payload = {"raw": raw_data, "source": "callback"}
    if extra_payload:
        payload.update(extra_payload)

    try:
        response = await manager.process_trigger(
            user,
            resolved_trigger,
            chat_id=callback.message.chat.id if callback.message else None,
            payload=payload,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "scene_trigger_failed",
            trigger=resolved_trigger,
            error=str(exc),
            user_id=user.id,
            raw=raw_data,
            exc_info=True,
        )
        return False

    if callback.message:
        await send_scene_response(
            callback.message,
            response,
            session=session,
            user=user,
            use_edit=use_edit,
            default_payload={"trigger": resolved_trigger, **payload},
        )
    await callback.answer()
    return True


__all__ = [
    "normalize_callback",
    "resolve_trigger",
    "send_scene_response",
    "escalate_to_manager",
    "try_process_command",
    "try_process_callback",
]
