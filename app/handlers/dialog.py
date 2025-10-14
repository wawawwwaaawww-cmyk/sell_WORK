"""Generic dialogue handlers that route free-form user text to the LLM."""

from typing import Optional, Dict, Any

import structlog
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import settings
from app.handlers.scene_dispatcher import send_scene_response
from app.models import User
from app.scenes.scene_manager import SceneManager
from app.services.llm_service import LLMService
from app.services.logging_service import ConversationLoggingService
from app.services.script_service import ScriptService
from app.services.stt_service import SttService
 
router = Router()
logger = structlog.get_logger()
stt_service = SttService()
 
 
async def _try_answer_from_script(
    message: Message, text: str, user: User, session: Any
) -> bool:
    """Tries to find and send a scripted answer. Returns True if successful."""
    if not settings.scripts_enabled:
        return False

    try:
        script_service = ScriptService(session)
        candidates = await script_service.search_similar_scripts(
            query_text=text, top_k=settings.retrieval_top_k
        )

        if not candidates:
            return False

        # Filter by threshold
        candidates = [c for c in candidates if c["similarity"] >= settings.retrieval_threshold]
        if not candidates:
            return False
            
        # LLM Validation
        llm_service = LLMService(session=session, user=user)
        is_relevant, best_answer = await llm_service.validate_script_relevance(
            user_query=text, candidates=candidates[:settings.judge_max_candidates]
        )

        if is_relevant and best_answer:
            await message.answer(best_answer)
            logger.info("Responded from script.", user_id=user.id, query=text)
            return True

    except Exception as e:
        logger.error("Error in script answering pipeline.", exc_info=True, user_id=user.id)

    return False


async def _process_text_payload(
    message: Message,
    text_payload: str,
    user: User,
    **kwargs: Dict[str, Any],
) -> None:
    """Process the text payload from any source (text, voice)."""
    session = kwargs.get("session")
    if not session:
        logger.warning("process_text_missing_session", user_id=user.id)
        return

    if await _try_answer_from_script(message, text_payload, user, session):
        return

    conversation_logger = ConversationLoggingService(session)
    await conversation_logger.log_user_message(
        user_id=user.id,
        text=text_payload,
        metadata={"source": "text_input"},
        bot=message.bot,
        user=user,
        telegram_user=message.from_user,
        source_message=message,
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

    if not user:
        logger.warning(
            "text_message_missing_user",
            chat_id=message.chat.id if message.chat else None,
        )
        return
 
    await _process_text_payload(message, text_payload, user, **kwargs)
 
 
@router.message(F.voice)
async def handle_user_voice_message(
    message: Message,
    state: FSMContext,
    user: Optional[User] = None,
    **kwargs: Dict[str, Any],
) -> None:
    """Handle voice messages by transcribing and processing them as text."""
    if not message.voice:
        return
 
    if state:
        current_state = await state.get_state()
        if current_state:
            logger.info(
                "voice_message_skipped_due_to_state",
                state=current_state,
                user_id=getattr(user, "id", None),
            )
            return
 
    if not user:
        logger.warning(
            "voice_message_missing_user",
            chat_id=message.chat.id if message.chat else None,
        )
        return
 
    transcribed_text = await stt_service.transcribe_audio(
        bot=message.bot, file_id=message.voice.file_id
    )
 
    if not transcribed_text:
        logger.warning("voice_message_transcription_failed", user_id=user.id)
        await message.answer(
            "Не удалось распознать ваше голосовое сообщение. Попробуйте еще раз или напишите текстом."
        )
        return
 
    logger.info(
        "voice_message_transcribed",
        user_id=user.id,
        text_length=len(transcribed_text),
    )
 
    await _process_text_payload(message, transcribed_text, user, **kwargs)
