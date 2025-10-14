"""Survey handlers for the 5-question survey system."""

from typing import Dict, Any, Optional, List
import json

import structlog
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext

from app.models import User, FunnelStage
from app.services.user_service import UserService
from app.services.survey_service import SurveyService
from app.services.event_service import EventService
from app.services.llm_service import LLMService, LLMContext
from app.services.product_matching_service import ProductMatchingService
from app.services.survey_offer_service import SurveyOfferService
from app.services.logging_service import ConversationLoggingService
from app.utils.callbacks import Callbacks, CallbackData
from app.handlers.scene_dispatcher import try_process_callback
from app.handlers.consultation import start_consultation_booking


router = Router()
logger = structlog.get_logger()


def _md_escape(value: str) -> str:
    """Escape characters that break Telegram Markdown."""
    if not value:
        return ""
    replacements = {
        "\\": "\\\\",
        "_": "\\_",
        "*": "\\*",
        "[": "\\[",
        "]": "\\]",
        "(": "\\(",
        ")": "\\)",
        "~": "\\~",
        "`": "\\`",
        ">": "\\>",
        "#": "\\#",
        "+": "\\+",
        "-": "\\-",
        "=": "\\=",
        "|": "\\|",
        "{": "\\{",
        "}": "\\}",
        ".": "\\.",
        "!": "\\!",
    }
    escaped = value
    for char, replacement in replacements.items():
        escaped = escaped.replace(char, replacement)
    return escaped


def _format_price_value(amount) -> str:
    """Format decimal price with thousand delimiter."""
    if amount is None:
        return "-"
    try:
        value = float(amount)
    except (TypeError, ValueError):
        return str(amount)
    if abs(value - int(value)) < 1e-6:
        return f"{int(value):,}".replace(",", " ")
    return f"{value:,.2f}".replace(",", " ")


def _extract_value_props(raw_props) -> List[str]:
    """Normalize value props to a list of strings."""
    if not raw_props:
        return []
    if isinstance(raw_props, str):
        try:
            parsed = json.loads(raw_props)
            return _extract_value_props(parsed)
        except json.JSONDecodeError:
            return [raw_props]
    if isinstance(raw_props, dict):
        items: List[str] = []
        for value in raw_props.values():
            items.extend(_extract_value_props(value))
        return items
    if isinstance(raw_props, (list, tuple, set)):
        result: List[str] = []
        for item in raw_props:
            result.extend(_extract_value_props(item))
        return result
    return [str(raw_props)]


def _build_product_card_text(product, score: float, explanation: str) -> str:
    """Render product recommendation block."""
    name = _md_escape(product.name or "Программа")
    short_desc = _md_escape(product.short_desc or "")
    value_props = [
        f"• {_md_escape(prop)}"
        for prop in _extract_value_props(product.value_props)[:2]
    ]
    price_text = _format_price_value(product.price)
    currency = _md_escape((product.currency or "RUB").upper())
    lines = [
        "🎯 **Подобрали программу для тебя:**",
        f"**{name}**",
    ]
    if short_desc:
        lines.append(short_desc)
    if value_props:
        lines.extend(value_props)
    lines.append(f"💵 Стоимость: {price_text} {currency}")
    lines.append(f"✅ Совпадение: {int(round(score * 100))}%")
    if explanation:
        lines.append(f"📌 Почему: {_md_escape(explanation)}")
    return "\n".join(lines)


async def _render_survey_step(
    callback: CallbackQuery,
    *,
    session,
    user: User,
    text: str,
    reply_markup=None,
    parse_mode: Optional[str] = "Markdown",
    metadata: Optional[dict] = None,
    prefer_edit: bool = True,
) -> None:
    """Render survey response respecting message history configuration."""
    message = callback.message
    if message is None:
        return

    if session:
        service = ConversationLoggingService(session)
        await service.send_or_edit(
            message,
            text=text,
            user_id=user.id,
            user=user,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            metadata=metadata,
            prefer_edit=prefer_edit,
        )
    else:
        await message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )


@router.callback_query(F.data.in_({Callbacks.SURVEY_START, Callbacks.SURVEY_START_FROM_OFFER}))
async def start_survey(callback: CallbackQuery, user: User, user_service: UserService, **kwargs):
    """Start the survey process."""
    try:
        session = kwargs.get("session")
        scenario_handled = await try_process_callback(callback, session=session, user=user)
        # Update funnel stage
        await user_service.advance_funnel_stage(user, FunnelStage.SURVEYED)
        
        # Log event
        event_service = EventService(session)
        event_type = "survey_started"
        if callback.data == Callbacks.SURVEY_START_FROM_OFFER:
            event_type = "survey_started_from_invite"

        await event_service.log_event(
            user_id=user.id,
            event_type=event_type,
            payload={"attempt": user.offer_attempt}
        )
        
        # Get first question
        survey_service = SurveyService(session)
        # Clear previous answers before starting a new survey
        await survey_service.clear_user_answers(user.id)
        
        question = await survey_service.get_question("q1")
        
        if not question:
            await callback.answer("Ошибка загрузки вопросов")
            return
        
        # Create keyboard with options
        keyboard = InlineKeyboardBuilder()
        for answer_code, option in question["options"].items():
            keyboard.add(InlineKeyboardButton(
                text=option["text"],
                callback_data=f"survey:q1:{answer_code}"
            ))
        keyboard.adjust(1)
        
        question_text = question["text"].strip()
        sections = ["📋 **Анкета для подбора программы**"]

        if question_text:
            sections.append(question_text)

        sections.append("*Вопрос 1 из 5*")

        survey_text = "\n\n".join(sections)
        
        await _render_survey_step(
            callback,
            session=session,
            user=user,
            text=survey_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown",
            metadata={"context": "survey_start", "question": "q1"},
        )
        
        if not scenario_handled:
            await callback.answer("📋 Начинаем анкету!")
        
    except Exception as e:
        logger.error("Error starting survey", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка при запуске анкеты")


# Set of affirmative answers to trigger consultation booking
AFFIRMATIVE_ANSWERS = {
    "yes", "да", "давай", "хорошо", "запиши", "готов", "согласен", "ок", "го", "поехали",
    "конечно", "ага", "угу", "хочу", "записывай", "естественно"
}


@router.callback_query(F.data.startswith("survey:q"))
async def handle_survey_answer(
    callback: CallbackQuery,
    user: User,
    user_service: UserService,
    state: FSMContext,
    **kwargs,
):
    """Handle survey answer."""
    try:
        session = kwargs.get("session")

        # Parse callback data
        parts = callback.data.split(":")
        if len(parts) < 3:
            await callback.answer("Неверный формат ответа")
            return
        
        question_code = parts[1]
        answer_code = parts[2]
        
        # Save answer
        survey_service = SurveyService(session)
        await survey_service.save_answer(user.id, question_code, answer_code)
        
        # Log event
        event_service = EventService(session)
        await event_service.log_survey_answer(
            user_id=user.id,
            question=question_code,
            answer=answer_code,
            points=0  # Points calculated in service
        )
        
        # Get confirmation text
        confirmation = await survey_service.get_confirmation_text(question_code, answer_code)

        # --- New logic for Q5 ---
        if question_code == "q5":
            question = await survey_service.get_question(question_code)
            answer_text = question.get("options", {}).get(answer_code, {}).get("text", "").lower()
            
            # Check if the answer is affirmative
            if any(word in answer_text for word in AFFIRMATIVE_ANSWERS) or answer_code == 'yes':
                await callback.message.edit_text("Отлично! Давайте подберем удобное время для консультации.")
                await start_consultation_booking(callback.message, state, user, session)
                return

        # Check if more questions remain
        next_question_code = await survey_service.get_next_question_code(user.id)
        
        if next_question_code:
            # Show next question
            await show_next_question(
                callback, user, survey_service, next_question_code, confirmation, session=session
            )
        else:
            # Survey complete - show results
            await complete_survey(callback, user, user_service, survey_service, confirmation, session=session)
        
    except Exception as e:
        logger.error("Error handling survey answer", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка при сохранении ответа")


async def show_next_question(
    callback: CallbackQuery,
    user: User,
    survey_service: SurveyService,
    question_code: str,
    confirmation: str,
    session=None,
):
    """Show next survey question."""
    session = session or getattr(survey_service, "session", None)

    question = await survey_service.get_question(question_code)
    if not question:
        await callback.answer("Ошибка загрузки следующего вопроса")
        return
    
    # Determine question number
    question_num = int(question_code[1:])
    
    # Create keyboard
    keyboard = InlineKeyboardBuilder()
    for answer_code, option in question["options"].items():
        keyboard.add(InlineKeyboardButton(
            text=option["text"],
            callback_data=f"survey:{question_code}:{answer_code}"
        ))
    keyboard.adjust(1)
    
    confirmation_text = confirmation.strip()
    question_text = question["text"].strip()
    sections = []

    if confirmation_text:
        sections.append(confirmation_text)

    normalized_confirmation = " ".join(confirmation_text.split()) if confirmation_text else ""
    normalized_question = " ".join(question_text.split()) if question_text else ""
    includes_question = bool(normalized_question) and normalized_question in normalized_confirmation

    if question_text and not includes_question:
        sections.append(question_text)

    question_marker = f"*Вопрос {question_num} из 5*"
    if question_marker not in confirmation_text:
        sections.append(question_marker)

    survey_text = "\n\n".join(sections)
    
    await _render_survey_step(
        callback,
        session=session,
        user=user,
        text=survey_text,
        reply_markup=keyboard.as_markup(),
        parse_mode="Markdown",
        metadata={"context": "survey_question", "question": question_code},
    )
    
    await callback.answer()


async def complete_survey(
    callback: CallbackQuery,
    user: User,
    user_service: UserService,
    survey_service: SurveyService,
    confirmation: str,
    session=None,
):
    """Complete survey and show results."""
    try:
        session = session or getattr(survey_service, "session", None)

        # Generate summary
        summary = await survey_service.generate_summary(user.id)
        
        # Update user segment
        await user_service.update_user_segment(user, summary["total_score"])
        
        # Log completion
        try:
            event_service = EventService(survey_service.session)
            await event_service.log_event(
                user_id=user.id,
                event_type="survey_completed",
                payload=summary
            )
            # Mark survey as completed for offer logic
            survey_offer_service = SurveyOfferService(session, user_service)
            await survey_offer_service.mark_survey_completed(user)
        except Exception as log_error:
            logger.warning("Failed to log survey completion", error=str(log_error), user_id=user.id)
        
        sanitized_confirmation = confirmation.strip() if confirmation else ""
        question_four = survey_service.questions.get("q4", {})
        question_four_text = question_four.get("text", "")
        if sanitized_confirmation and question_four_text:
            plain_q4 = question_four_text.replace("*", "").strip()
            sanitized_confirmation = sanitized_confirmation.replace(question_four_text, "")
            if plain_q4:
                sanitized_confirmation = sanitized_confirmation.replace(plain_q4, "")
            sanitized_confirmation = sanitized_confirmation.strip()

        sections = [
            "🎉 **Анкета завершена!**",
            "📊 **Твой профиль:**",
            summary["profile_summary"],
            f"🎯 **Категория:** {summary['segment_description']}",
            f"📈 **Балл готовности:** {summary['total_score']}/13",
            "💡 *На основе твоих ответов я подберу оптимальную программу обучения!*",
        ]

        if sanitized_confirmation:
            sections.append(sanitized_confirmation)

        matching_service = ProductMatchingService(session)
        match_result = await matching_service.match_for_user(
            user,
            trigger="survey_complete",
            log_result=True,
        )

        metadata = {
            "context": "survey_complete",
            "segment": summary["segment"],
            "score": summary["total_score"],
            "product_id": None,
            "product_score": match_result.score,
        }

        keyboard = InlineKeyboardBuilder()
        if match_result.best_product:
            product_card = _build_product_card_text(
                match_result.best_product,
                match_result.score,
                match_result.explanation,
            )
            sections.append(product_card)
            metadata["product_id"] = match_result.best_product.id
            cta_text = "🔥 Хочу программу"
            keyboard.add(
                InlineKeyboardButton(
                    text=cta_text,
                    callback_data=Callbacks.MANAGER_REQUEST,
                )
            )
            landing = match_result.best_product.landing_url or match_result.best_product.payment_landing_url
            if landing:
                keyboard.add(
                    InlineKeyboardButton(
                        text="🌐 Подробнее",
                        url=landing,
                    )
                )
            keyboard.add(
                InlineKeyboardButton(
                    text="💬 Задать вопросы",
                    callback_data="llm:ask_questions",
                )
            )
            keyboard.adjust(1)
            sections.append("Готов обсудить детали? 🚀")
        else:
            sections.append(
                "Пока не вижу идеального курса, но можем подобрать решение на консультации."
            )
            keyboard.add(
                InlineKeyboardButton(
                    text="📅 Записаться на консультацию",
                    callback_data=Callbacks.CONSULT_OFFER,
                )
            )
            keyboard.add(
                InlineKeyboardButton(
                    text="💬 Задать вопросы боту",
                    callback_data="llm:ask_questions",
                )
            )
            keyboard.adjust(1)

        results_text = "\n\n".join(part for part in sections if part)
        
        if match_result.best_product and match_result.best_product.media:
            for media in match_result.best_product.media:
                if media.media_type == 'photo':
                    await callback.message.answer_photo(media.file_id)
                elif media.media_type == 'video':
                    await callback.message.answer_video(media.file_id)
                elif media.media_type == 'document':
                    await callback.message.answer_document(media.file_id)

        await _render_survey_step(
            callback,
            session=session,
            user=user,
            text=results_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown",
            metadata=metadata,
        )
        
        await callback.answer("✅ Анкета завершена!")
        
    except Exception as e:
        logger.error("Error completing survey", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Ошибка при завершении анкеты")


@router.callback_query(F.data.startswith("llm:"))
async def handle_llm_interaction(callback: CallbackQuery, user: User, **kwargs):
    """Handle LLM-powered interactions after survey."""
    try:
        # Acknowledge the callback immediately to prevent timeout
        await callback.answer()

        session = kwargs.get("session")
        action_parts = callback.data.split(":", 1)
        action = action_parts[1] if len(action_parts) > 1 else ""
        
        # Get survey summary for LLM context
        survey_service = SurveyService(session)
        summary = await survey_service.generate_summary(user.id)
        
        # Build LLM context
        context = LLMContext(
            user=user,
            messages_history=[],
            survey_summary=summary["profile_summary"],
            funnel_stage=user.funnel_stage
        )
        
        # Generate LLM response based on action
        llm_service = LLMService()
        
        if action == "discuss_programs":
            # Add user message to context for program discussion
            context.messages_history.append({
                "role": "user",
                "text": "Расскажи мне о подходящих программах обучения"
            })
        elif action == "ask_questions":
            context.messages_history.append({
                "role": "user",
                "text": "У меня есть вопросы о криптовалютах"
            })
        elif action:
            context.messages_history.append({
                "role": "user",
                "text": f"Мне нужен ответ по действию: {action}",
            })
        else:
            context.messages_history.append({
                "role": "user",
                "text": "Подскажи, какие следующие шаги ты рекомендуешь",
            })
        
        response = await llm_service.generate_response(context)
        
        # Create keyboard from LLM response
        keyboard = InlineKeyboardBuilder()
        if response.buttons:
            for button in response.buttons:
                if isinstance(button, dict):
                    text = button.get("text", "Continue")
                    callback_data = button.get("callback_data") or button.get("callback")
                else:
                    text = "Продолжить"
                    callback_data = str(button)
                
                keyboard.add(InlineKeyboardButton(
                    text=text,
                    callback_data=callback_data
                ))
        keyboard.adjust(1)
        
        await _render_survey_step(
            callback,
            session=session,
            user=user,
            text=response.reply_text,
            reply_markup=keyboard.as_markup() if response.buttons else None,
            parse_mode="HTML",
            metadata={"context": "survey_llm_followup", "action": action},
        )
        
    except Exception as e:
        logger.error("Error in LLM interaction", error=str(e), user_id=user.id, exc_info=True)
        # Use a simple text answer as a fallback, as the original callback is already answered
        await callback.message.answer("Произошла ошибка. Попробуйте еще раз.")


@router.callback_query(F.data == Callbacks.SURVEY_OFFER_LATER)
async def handle_survey_offer_later(callback: CallbackQuery, user: User, **kwargs):
    """Handle the 'Later' button on a survey offer."""
    try:
        session = kwargs.get("session")
        event_service = EventService(session)
        await event_service.log_event(
            user_id=user.id,
            event_type="survey_invite_clicked",
            payload={"btn": "later", "attempt": user.offer_attempt},
        )
        await callback.message.edit_text("Хорошо, вернемся к этому позже.", reply_markup=None)
        await callback.answer()
    except Exception as e:
        logger.error("Error handling survey offer 'Later'", error=str(e), user_id=user.id)
        await callback.answer("Произошла ошибка.")


def register_handlers(dp):
    """Register survey handlers."""
    dp.include_router(router)


async def start_survey_via_message(
    message: Message,
    *,
    session,
    user: User,
    user_service: UserService,
) -> bool:
    """Start the survey flow using a regular text message as an entry point."""

    logger.info(
        "survey_start_via_message_called",
        user_id=getattr(user, "id", None),
    )

    conversation_logger = ConversationLoggingService(session) if session else None
    try:
        await user_service.advance_funnel_stage(user, FunnelStage.SURVEYED)

        event_service = EventService(session)
        await event_service.log_event(
            user_id=user.id,
            event_type="survey_started",
            payload={"entry": "message"},
        )

        survey_service = SurveyService(session)
        await survey_service.clear_user_answers(user.id)

        question = await survey_service.get_question("q1")
        if not question:
            logger.error("survey_start_via_message_no_question", user_id=user.id)
            await message.answer("Ошибка загрузки вопросов")
            return False

        keyboard = InlineKeyboardBuilder()
        for answer_code, option in question["options"].items():
            keyboard.add(InlineKeyboardButton(
                text=option["text"],
                callback_data=f"survey:q1:{answer_code}"
            ))
        keyboard.adjust(1)

        question_text = question["text"].strip()
        sections = ["📋 **Анкета для подбора программы**"]

        if question_text:
            sections.append(question_text)

        sections.append("*Вопрос 1 из 5*")

        survey_text = "\n\n".join(sections)

        if conversation_logger:
            await conversation_logger.send_or_edit(
                message,
                text=survey_text,
                user_id=user.id,
                user=user,
                reply_markup=keyboard.as_markup(),
                parse_mode="Markdown",
                metadata={"context": "survey_start", "question": "q1", "entry": "message"},
                prefer_edit=False,
            )
        else:
            await message.answer(
                survey_text,
                reply_markup=keyboard.as_markup(),
                parse_mode="Markdown",
            )

        logger.info("survey_start_via_message_completed", user_id=user.id)
        return True

    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error(
            "survey_start_via_message_error",
            user_id=getattr(user, "id", None),
            error=str(exc),
            exc_info=True,
        )
        await message.answer("Произошла ошибка при запуске анкеты")
        return False
