"""Generic dialogue handlers that route free-form user text to the LLM."""

import asyncio
import random
from datetime import datetime
from typing import Optional, Dict, Any

import structlog
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.config import settings
from app.models import User, LeadStatus
from app.services.logging_service import ConversationLoggingService
from app.services.manual_dialog_service import manual_dialog_service
# from app.services.script_service import ScriptService
from app.services.stt_service import SttService
from app.services.sales_dialog_service import SalesDialogService
from app.services.llm_service import LLMService
from app.repositories.user_repository import UserRepository
from app.services.purchase_intent_service import PurchaseIntentService
from app.services.inquiry_intent_service import InquiryIntentService
from app.services.lead_profile_service import LeadProfileService
from app.services.lead_service import LeadService
from app.services.manager_notification_service import ManagerNotificationService
from app.safety.validator import SafetyValidator
 
router = Router()
logger = structlog.get_logger()
stt_service = SttService()
safety_validator = SafetyValidator()


async def _simulate_typing(bot, chat_id: int, *, min_delay: float = 3.0, max_delay: float = 7.0) -> None:
    """Simulate human-like typing indicator for a random duration."""
    delay = random.uniform(min_delay, max_delay)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + delay

    while True:
        await bot.send_chat_action(chat_id=chat_id, action="typing")
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(4.0, remaining))


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


async def _handle_purchase_intent(
    message: Message,
    text_payload: str,
    user: User,
    session: Any,
    conversation_logger: ConversationLoggingService,
) -> bool:
    """Detect purchase intent and route to managers if needed."""
    intent_service = PurchaseIntentService()

    history = await conversation_logger.get_last_messages(user.id, limit=6)
    context_parts = []
    for item in history:
        role = item.get("role")
        text = item.get("text")
        if not text:
            continue
        prefix = "user" if role == "user" else "bot"
        context_parts.append(f"{prefix}: {text}")
    context_str = " | ".join(context_parts[-4:])

    try:
        has_intent = await intent_service.has_purchase_intent(
            text_payload,
            context=context_str,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("purchase_intent_detection_failed", error=str(exc), user_id=user.id)
        has_intent = False

    if not has_intent:
        return False

    lead_service = LeadService(session)
    existing_leads = await lead_service.repository.get_user_leads(user.id)
    active_statuses = {LeadStatus.NEW, LeadStatus.TAKEN, LeadStatus.ASSIGNED}
    existing_active = [lead for lead in existing_leads if getattr(lead, "status", None) in active_statuses]

    user_repo = UserRepository(session)
    manager_display = "не указан"
    if existing_active:
        sorted_leads = sorted(
            existing_active,
            key=lambda l: (getattr(l, "updated_at", None) or getattr(l, "created_at", None) or datetime.min),
            reverse=True,
        )
        for prev_lead in sorted_leads:
            manager_id_candidate = getattr(prev_lead, "assigned_manager_id", None) or getattr(prev_lead, "assignee_id", None)
            if manager_id_candidate:
                username = None
                manager_user = await user_repo.get_by_telegram_id(manager_id_candidate)
                if manager_user and manager_user.username:
                    username = manager_user.username
                if username:
                    manager_display = f"@{username}"
                elif manager_user and (manager_user.first_name or manager_user.last_name):
                    manager_display = " ".join(filter(None, [manager_user.first_name, manager_user.last_name]))
                else:
                    manager_display = f"ID {manager_id_candidate}"
                break

    profile_service = LeadProfileService(session)
    profile = await profile_service.get_or_create(user)
    is_repeat = bool(existing_active)
    if is_repeat:
        summary = (
            f"Повторный лид. Предыдущий менеджер: {manager_display}\n\n"
            f"Последнее сообщение: \"{text_payload}\""
        )
    else:
        summary = profile.summary_text or (
            f"Пользователь сообщил намерение оформить обучение. Последнее сообщение: \"{text_payload}\""
        )

    lead = await lead_service.create_lead_from_user(
        user,
        trigger_event="auto_purchase_intent_repeat" if is_repeat else "auto_purchase_intent",
        conversation_summary=summary,
    )

    manager_service = ManagerNotificationService(message.bot, session)
    await manager_service.notify_new_lead(lead, user)

    await _simulate_typing(message.bot, message.chat.id)
    if is_repeat:
        acknowledgement = (
            "Я передал вашу заявку менеджерам повторно. "
            "Эксперт свяжется с вами в ближайшее время."
        )
        ack_source = "purchase_intent_ack_repeat"
    else:
        acknowledgement = (
            "Отлично, я передал вашу заявку менеджерам команды Азата. "
            "Они свяжутся с вами, чтобы помочь с обучением и ответить на вопросы."
        )
        ack_source = "purchase_intent_ack_new"
    sent_message = await message.answer(acknowledgement)
    await conversation_logger.log_bot_message(
        user_id=user.id,
        text=acknowledgement,
        metadata={"source": ack_source, "lead_id": lead.id},
        bot=message.bot,
        user=user,
        source_message=sent_message,
    )

    logger.info(
        "purchase_intent_lead_created",
        user_id=user.id,
        lead_id=lead.id,
        repeat=is_repeat,
        previous_manager=manager_display if is_repeat else None,
    )
    return True


async def _handle_inquiry_intent(
    message: Message,
    text_payload: str,
    user: User,
    session: Any,
    conversation_logger: ConversationLoggingService,
) -> bool:
    """Detect information-request intent and notify managers."""
    intent_service = InquiryIntentService()

    history = await conversation_logger.get_last_messages(user.id, limit=6)
    context_parts = []
    for item in history:
        role = item.get("role")
        text = item.get("text")
        if not text:
            continue
        prefix = "user" if role == "user" else "bot"
        context_parts.append(f"{prefix}: {text}")
    context_str = " | ".join(context_parts[-4:])

    try:
        has_intent = await intent_service.has_info_intent(text_payload, context=context_str)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("inquiry_intent_detection_failed", error=str(exc), user_id=user.id)
        has_intent = False

    if not has_intent:
        return False

    lead_service = LeadService(session)
    existing_leads = await lead_service.repository.get_user_leads(user.id)
    active_statuses = {LeadStatus.NEW, LeadStatus.TAKEN, LeadStatus.ASSIGNED}
    existing_active = [lead for lead in existing_leads if getattr(lead, "status", None) in active_statuses]

    user_repo = UserRepository(session)
    manager_display = "не указан"
    if existing_active:
        sorted_leads = sorted(
            existing_active,
            key=lambda l: (getattr(l, "updated_at", None) or getattr(l, "created_at", None) or datetime.min),
            reverse=True,
        )
        for prev_lead in sorted_leads:
            manager_id_candidate = getattr(prev_lead, "assigned_manager_id", None) or getattr(prev_lead, "assignee_id", None)
            if manager_id_candidate:
                username = None
                manager_user = await user_repo.get_by_telegram_id(manager_id_candidate)
                if manager_user and manager_user.username:
                    username = manager_user.username
                if username:
                    manager_display = f"@{username}"
                elif manager_user and (manager_user.first_name or manager_user.last_name):
                    manager_display = " ".join(filter(None, [manager_user.first_name, manager_user.last_name]))
                else:
                    manager_display = f"ID {manager_id_candidate}"
                break

    profile_service = LeadProfileService(session)
    profile = await profile_service.get_or_create(user)
    is_repeat = bool(existing_active)
    if is_repeat:
        summary = (
            f"Повторный запрос. Предыдущий менеджер: {manager_display}\n\n"
            f"Последнее сообщение: \"{text_payload}\""
        )
    else:
        summary = profile.summary_text or (
            f"Пользователь запросил подробности о продукте. Последнее сообщение: \"{text_payload}\""
        )

    lead = await lead_service.create_lead_from_user(
        user,
        trigger_event="auto_info_request_repeat" if is_repeat else "auto_info_request",
        conversation_summary=summary,
    )

    manager_service = ManagerNotificationService(message.bot, session)
    await manager_service.notify_new_lead(lead, user)

    await _simulate_typing(message.bot, message.chat.id)
    if is_repeat:
        acknowledgement = (
            "Я передал ваш запрос эксперту повторно. Менеджер свяжется с вами в ближайшее время."
        )
        ack_source = "info_intent_ack_repeat"
    else:
        acknowledgement = (
            "Супер, я передам вас нашему эксперту. Менеджер свяжется с вами в течение 20 минут, хорошо?"
        )
        ack_source = "info_intent_ack_new"
    sent_message = await message.answer(acknowledgement)
    await conversation_logger.log_bot_message(
        user_id=user.id,
        text=acknowledgement,
        metadata={"source": ack_source, "lead_id": lead.id},
        bot=message.bot,
        user=user,
        source_message=sent_message,
    )

    logger.info(
        "info_intent_lead_created",
        user_id=user.id,
        lead_id=lead.id,
        repeat=is_repeat,
        previous_manager=manager_display if is_repeat else None,
    )
    return True


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

    if await _handle_purchase_intent(message, text_payload, user, session, conversation_logger):
        return

    if await _handle_inquiry_intent(message, text_payload, user, session, conversation_logger):
        return

    dialog_service = SalesDialogService(session=session, user=user)
    outcome = await dialog_service.generate_reply()

    if outcome.reply_text:
        await _simulate_typing(message.bot, message.chat.id)
        sent_message = await message.answer(outcome.reply_text)
        metadata = outcome.metadata
        await conversation_logger.log_bot_message(
            user_id=user.id,
            text=outcome.reply_text,
            metadata=metadata,
            bot=message.bot,
            user=user,
            source_message=sent_message,
        )

    logger.info(
        "text_message_processed_by_sales_dialog",
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
    # If this is a manager working in manual dialog mode, skip automatic handling
    manager_session = (
        manual_dialog_service.get_session_by_manager(message.from_user.id)
        if message.from_user
        else None
    )
    if manager_session:
        logger.info(
            "Manual manager message intercepted, skipping LLM.",
            manager_id=message.from_user.id,
            target_user_id=manager_session.user_id,
        )
        return

    session = kwargs.get("session")
    if not session or not user:
        logger.warning("Text message handler missing session or user.", user_id=getattr(user, "id", None))
        return

    if manual_dialog_service.is_user_in_manual_mode(user.id):
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
        metadata={"source": "sales_dialog_input"},
        bot=message.bot,
        user=user,
        telegram_user=message.from_user,
        source_message=message,
    )

    if await _handle_purchase_intent(message, text_payload, user, session, logging_service):
        return

    dialog_service = SalesDialogService(session=session, user=user)
    outcome = await dialog_service.generate_reply()

    if outcome.reply_text:
        await _simulate_typing(message.bot, message.chat.id)
        sent_message = await message.answer(outcome.reply_text)
        metadata = outcome.metadata
        await logging_service.log_bot_message(
            user_id=user.id,
            text=outcome.reply_text,
            metadata=metadata,
            bot=message.bot,
            user=user,
            source_message=sent_message,
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
