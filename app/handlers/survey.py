"""Survey handlers for the 5-question survey system."""

from typing import Dict, Any, Optional

import structlog
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import User, FunnelStage
from app.services.user_service import UserService
from app.services.survey_service import SurveyService
from app.services.event_service import EventService
from app.services.llm_service import LLMService, LLMContext
from app.services.logging_service import ConversationLoggingService
from app.utils.callbacks import Callbacks, CallbackData
from app.handlers.scene_dispatcher import try_process_callback


router = Router()
logger = structlog.get_logger()


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


@router.callback_query(F.data == Callbacks.SURVEY_START)
async def start_survey(callback: CallbackQuery, user: User, user_service: UserService, **kwargs):
    """Start the survey process."""
    try:
        session = kwargs.get("session")
        scenario_handled = await try_process_callback(callback, session=session, user=user)
        # Update funnel stage
        await user_service.advance_funnel_stage(user, FunnelStage.SURVEYED)
        
        # Log event
        event_service = EventService(session)
        await event_service.log_event(
            user_id=user.id,
            event_type="survey_started",
            payload={}
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
        
        survey_text = f"""📋 **Анкета для подбора программы**

{question["text"]}

*Вопрос 1 из 5*"""
        
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


@router.callback_query(F.data.startswith("survey:q"))
async def handle_survey_answer(callback: CallbackQuery, user: User, user_service: UserService, **kwargs):
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
        answer_record = await survey_service.save_answer(user.id, question_code, answer_code)

        # Log event
        event_service = EventService(session)
        await event_service.log_survey_answer(
            user_id=user.id,
            question=question_code,
            answer=answer_code,
            points=answer_record.points
        )
        
        # Get confirmation text
        confirmation = await survey_service.get_confirmation_text(question_code, answer_code)
        
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
    
    survey_text = f"""{confirmation}

{question["text"]}

*Вопрос {question_num} из 5*"""
    
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
        except Exception as log_error:
            logger.warning("Failed to log survey completion", error=str(log_error), user_id=user.id)
        
        answers_map = summary.get("answers", {})
        program_info = summary.get("program")
        confirmation_text = confirmation

        if program_info:
            confirmation_text = confirmation.replace("{program_result}", program_info["key_result"])

        # Create results text
        results_text = f"""{confirmation_text}

🎉 **Анкета завершена!**

📊 **Твой профиль:**
{summary["profile_summary"]}

🎯 **Категория:** {summary["segment_description"]}
📈 **Балл готовности:** {summary["total_score"]}/13

💡 *На основе твоих ответов я подберу оптимальную программу обучения!*

Давай обсудим следующие шаги? 🚀"""

        # Create keyboard for next actions
        keyboard = InlineKeyboardBuilder()

        final_answer = answers_map.get("q5")
        program_callback = None
        if program_info:
            program_callback = f"survey:program:{program_info['code']}"

        if final_answer == "yes":
            keyboard.add(InlineKeyboardButton(
                text="📅 Подобрать время",
                callback_data=Callbacks.CONSULT_OFFER
            ))
            if program_callback:
                keyboard.add(InlineKeyboardButton(
                    text="📘 Узнать про программу",
                    callback_data=program_callback
                ))
        elif final_answer == "no":
            keyboard.add(InlineKeyboardButton(
                text="📞 Бесплатная консультация",
                callback_data=Callbacks.CONSULT_OFFER
            ))
            if program_callback:
                keyboard.add(InlineKeyboardButton(
                    text="📘 Посмотреть программу",
                    callback_data=program_callback
                ))
        else:
            if summary["segment"] == "hot":
                keyboard.add(InlineKeyboardButton(
                    text="📞 Записаться на консультацию",
                    callback_data=Callbacks.CONSULT_OFFER
                ))
                keyboard.add(InlineKeyboardButton(
                    text="💬 Обсудить программы",
                    callback_data="llm:discuss_programs"
                ))
            elif summary["segment"] == "warm":
                keyboard.add(InlineKeyboardButton(
                    text="💬 Подобрать программу",
                    callback_data="llm:discuss_programs"
                ))
                keyboard.add(InlineKeyboardButton(
                    text="📞 Консультация с экспертом",
                    callback_data=Callbacks.CONSULT_OFFER
                ))
            else:  # cold
                keyboard.add(InlineKeyboardButton(
                    text="📚 Получить материалы для изучения",
                    callback_data="materials:educational"
                ))
                keyboard.add(InlineKeyboardButton(
                    text="💬 Задать вопросы",
                    callback_data="llm:ask_questions"
                ))

        keyboard.adjust(1)
        
        await _render_survey_step(
            callback,
            session=session,
            user=user,
            text=results_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown",
            metadata={
                "context": "survey_complete",
                "segment": summary["segment"],
                "score": summary["total_score"],
                "program": program_info["code"] if program_info else None,
            },
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
        action = callback.data.split(":", 1)
        
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


@router.callback_query(F.data.startswith("survey:program:"))
async def handle_program_details(callback: CallbackQuery, user: User, **kwargs):
    """Show detailed information about the recommended program."""
    try:
        await callback.answer()

        session = kwargs.get("session")
        survey_service = SurveyService(session)
        summary = await survey_service.generate_summary(user.id)
        program_info = summary.get("program")

        if not program_info:
            await callback.message.answer(
                "❌ Не удалось получить информацию о программе. Попробуйте позже."
            )
            return

        requested_code = callback.data.split(":")[-1]
        logger.info(
            "Program details requested",
            user_id=user.id,
            requested_code=requested_code,
            recommended=program_info.get("code") if program_info else None,
        )
        if requested_code != program_info.get("code"):
            program_info = survey_service.determine_program_recommendation(
                summary.get("answers", {}),
                summary.get("total_score", 0)
            )

        details_text = f"""📘 <b>{program_info['name']}</b>

{program_info['description']}

🤝 Готов обсудить варианты участия или задать вопросы?"""

        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="📅 Записаться на созвон",
            callback_data=Callbacks.CONSULT_OFFER
        ))
        keyboard.add(InlineKeyboardButton(
            text="💬 Задать вопросы",
            callback_data="llm:ask_questions"
        ))
        keyboard.adjust(1)

        await callback.message.answer(
            details_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )

        logger.info(
            "Program details shown",
            user_id=user.id,
            program_code=program_info.get("code")
        )

    except Exception as e:
        logger.error("Error showing program details", error=str(e), user_id=user.id, exc_info=True)
        await callback.message.answer("Произошла ошибка при показе программы")


def register_handlers(dp):
    """Register survey handlers."""
    dp.include_router(router)