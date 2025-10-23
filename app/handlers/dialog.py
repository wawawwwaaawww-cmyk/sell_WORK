"""Generic dialogue handlers that route free-form user text to the LLM."""

from typing import Optional, Dict, Any

import structlog
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import settings
from app.models import User
from app.services.llm_service import LLMService
from app.services.logging_service import ConversationLoggingService
from app.services.manual_dialog_service import ManualDialogService
# from app.services.script_service import ScriptService
from app.services.stt_service import SttService
from app.safety.validator import SafetyValidator
 
router = Router()
logger = structlog.get_logger()
stt_service = SttService()
safety_validator = SafetyValidator()
 
 
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
    await message.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    session = kwargs.get("session")
    if not session:
        logger.warning("process_text_missing_session", user_id=user.id)
        return

    # if await _try_answer_from_script(message, text_payload, user, session):
    #     return

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

    llm_service = LLMService(session=session)
    response_text = await llm_service.get_response(text_payload, user.id)
    
    sanitized_text, _ = safety_validator.validate_response(response_text)

    if sanitized_text:
        sent_message = await message.answer(sanitized_text)
        await conversation_logger.log_bot_message(
            user_id=user.id,
            text=sanitized_text,
            metadata={"source": "llm_dialog"},
            bot=message.bot,
            user=user,
            telegram_user=message.from_user,
            sent_message=sent_message,
        )

    logger.info(
        "text_message_processed_by_llm",
        user_id=user.id,
    )


@router.message(F.text)
async def handle_text_message(
    message: Message,
    state: FSMContext,
    user: Optional[User] = None,
    **kwargs: Dict[str, Any],
) -> None:
    """Handle arbitrary text messages and forward them to the LLM."""
    session = kwargs.get("session")
    if not session or not user:
        logger.warning("Text message handler missing session or user.", user_id=getattr(user, "id", None))
        return

    manual_dialog_service = ManualDialogService(session)
    if await manual_dialog_service.is_manual_dialog_active(user.id):
        logger.info("Manual dialog is active, skipping LLM handler.", user_id=user.id)
        return

    if message.text is None:
        return

    text_payload = message.text.strip()
    if not text_payload or text_payload.startswith("/"):
        return

    current_state = await state.get_state()
    if current_state:
        logger.info(
            "Text message skipped due to active FSM state.",
            state=current_state,
            user_id=user.id,
        )
        return
    
    logging_service = ConversationLoggingService(session)
    await logging_service.log_user_message(
        user_id=user.id,
        text=message.text,
        metadata={"source": "llm_dialog_input"},
        bot=message.bot,
        user=user,
        telegram_user=message.from_user,
        source_message=message,
    )

    llm_service = LLMService(session=session)
    response_text = await llm_service.get_response(message.text, user.id)

    # The validation is now inside get_response, but we can double-check here if needed.
    # For now, we trust the service layer.
    
    if response_text:
        sent_message = await message.answer(response_text)
        await logging_service.log_bot_message(
            user_id=user.id,
            text=response_text,
            metadata={"source": "llm_dialog_output"},
            bot=message.bot,
            user=user,
            telegram_user=message.from_user,
            sent_message=sent_message,
        )


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
