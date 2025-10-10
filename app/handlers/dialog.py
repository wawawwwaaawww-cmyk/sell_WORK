"""Generic dialogue handlers that route free-form user text to the LLM."""

from typing import Optional, Dict, Any

import structlog
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.handlers.scene_dispatcher import send_scene_response
from app.models import User
from app.scenes.scene_manager import SceneManager
from app.services.logging_service import ConversationLoggingService

router = Router()
logger = structlog.get_logger()


@router.message(F.text)
async def handle_user_text_message(
    message: Message,
    state: FSMContext,
    user: Optional[User] = None,
    **kwargs: Dict[str, Any],
) -> None:
    """Handle arbitrary text messages and forward them to the scene manager."""

    if message.text is None:
        logger.debug("text_message_empty", chat_id=message.chat.id if message.chat else None)
        return

    text_payload = message.text.strip()
    if not text_payload:
        logger.debug("text_message_whitespace", chat_id=message.chat.id if message.chat else None)
        return

    if text_payload.startswith("/"):
        logger.debug("text_message_command_like", command=text_payload, chat_id=message.chat.id if message.chat else None)
        return

    if state:
        current_state = await state.get_state()
        if current_state:
            logger.info(
                "text_message_skipped_due_to_state",
                state=current_state,
                user_id=getattr(user, "id", None),
            )
            return

    session = kwargs.get("session")
    if not session or not user:
        logger.warning(
            "text_message_missing_context",
            has_session=bool(session),
            has_user=bool(user),
        )
        return

    conversation_logger = ConversationLoggingService(session)
    await conversation_logger.log_user_message(
        user_id=user.id,
        text=text_payload,
        metadata={"source": "text_input"},
    )

    manager = SceneManager(session)

    try:
        response = await manager.process_user_message(user, text_payload)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "scene_processing_failed",
            error=str(exc),
            user_id=user.id,
            exc_info=True,
        )
        await message.answer("Пока не могу ответить, но уже подключаю менеджера. Напишите вопрос ещё раз чуть позже 🙏")
        return

    await send_scene_response(
        message,
        response,
        session=session,
        user=user,
        default_event="user_message",
        default_payload={"source": "text_input"},
    )

    logger.info(
        "text_message_processed",
        user_id=user.id,
        scene=response.log_event.get("scene") if response.log_event else None,
        escalate=response.escalate,
    )
