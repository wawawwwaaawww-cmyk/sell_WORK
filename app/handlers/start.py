"""Start command and welcome flow handlers."""

import asyncio
from typing import Optional

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           Message)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.config import settings
from app.handlers.survey import start_survey_via_message
from app.models import User
from app.services.bonus_content_manager import BonusContentManager
from app.services.logging_service import ConversationLoggingService
from app.services.priority_analysis_service import (ConfirmationIntent,
                                                    PriorityAnalysisService,
                                                    PriorityIntent)
from app.services.user_service import UserService
from app.utils.callbacks import Callbacks

router = Router()
logger = structlog.get_logger()


class DeclinedSurveyStates(StatesGroup):
    """FSM states for strategy discussion after survey decline."""

    waiting_priority_choice = State()
    waiting_reliability_confirmation = State()
    waiting_growth_confirmation = State()


analysis_service = PriorityAnalysisService()

RELIABILITY_MESSAGE = """Круто, понимаю 👍 В криптовалютном рынке под «надёжность» обычно имеют в виду сохранение капитала с минимальной волатильностью.
 Вот несколько стратегий:
Стейблкоины (USDT, USDC, DAI) — хранятся в долларовой привязке, их курс почти не колеблется.

Стейкинг надёжных монет (например ETH) — можно получать 3–5% годовых, просто держа монету в сети.

Доходные продукты на стейблах — размещение стейблкоинов в проверенных DeFi-протоколах для пассивного дохода.

👉 Могу рассказать, как приобрести знания в данной области и сохранять капитал. Хочешь?"""

GROWTH_MESSAGE = """Отличный выбор 🔥 В крипторынке рост связан с более рискованными, но потенциально доходными инструментами.
 Например:
Топовые альткоины (ETH, BNB, SOL, DOT) — проекты с сильной экосистемой, которые могут расти в цене.

DeFi-токены — монеты протоколов, которые развиваются и дают возможность «поймать рост» на ранних стадиях.

NFT или GameFi-сектора — более рискованные направления, но иногда приносят кратный результат.

👉 Хочешь расскажу на чем наши ученики кратно умножили свой капитал и продолжают расти в финансовом плане?"""

RELIABILITY_DECLINE_MESSAGE = (
    "Окей, понял 🙂 Я никуда не тороплю. Давай просто останемся на связи — когда появится интерес или время, "
    "я помогу разобраться с нужными стратегиями. А пока можем поговорить о том, что тебе уже знакомо в крипте — с чего ты начинал?"
)

GROWTH_DECLINE_MESSAGE = (
    "Окей, понял тебя 🙂 Давай тогда пока оставим эту тему. Когда появится интерес — я помогу разобраться и подобрать стратегии "
    "под твои цели. Чтобы наше общение было более продуктивным, ответь на несколько вопросов, чтобы я всегда мог тебе помочь "
    "подобрать наиболее подходящий путь развития на криптовалютном рынке и дальше - "
)

APPLICATION_PROMPT_MESSAGE = (
    "📝 Отлично! Ниже кнопка со стандартной формой заявки. Заполни её, и я передам информацию команде."
)

CLARIFICATION_MESSAGE = (
    "Хочу убедиться, что правильно тебя понял. Что для тебя сейчас важнее: надёжность или возможность роста?"
)

CONFIRMATION_CLARIFICATION_MESSAGE = (
    "Подскажи, пожалуйста, продолжим? Можно ответить простым «да» или «нет»."
)

@router.message(Command("start"))
async def start_command(message: Message, **kwargs):
    """Handle /start command and offer a bonus."""
    try:
        session = kwargs.get("session")
        user_service = UserService(session)
        user = await user_service.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )

        welcome_text = """👋 Привет!
Добро пожаловать в чат школы Азата Валеева 🎉
Здесь ты найдёшь полезные материалы, подарки и специальные предложения.
Чтобы начать — жми кнопку ниже и получи свой бонус 🎁"""

        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="Получить бонус",
            callback_data="bonus:get_file"
        ))

        await message.answer(
            welcome_text,
            reply_markup=keyboard.as_markup()
        )

        logger.info("Start command processed, bonus offered", user_id=user.id)

    except Exception as e:
        logger.error("Error in start command", error=str(e), exc_info=True)
        await message.answer("Произошла ошибка. Попробуйте еще раз позже.")


@router.callback_query(F.data == "bonus:get_file")
async def send_bonus_file(callback: CallbackQuery, **kwargs):
    """Send the bonus file and a follow-up message."""
    try:
        await callback.answer("🎁 Отправляю ваш бонус...")

        bonus_file_path, bonus_caption = BonusContentManager.load_published_bonus()
        document = FSInputFile(bonus_file_path)

        await callback.message.answer_document(
            document,
            caption=bonus_caption
        )
        logger.info("Bonus file sent", user_id=callback.from_user.id)

        await asyncio.sleep(settings.bonus_followup_delay)

        user_name = callback.from_user.first_name or "друг"
        follow_up_text = (
            f"{user_name}, хочу предложить тебе сделать следующий шаг — "
            "подобрать инструмент инвестирования, который лучше всего подойдет именно тебе. "
            "Это может быть что-то консервативное для сохранения капитала или более активное для роста. "
            "Готов начать?"
        )

        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="🤑 ДА",
            callback_data=Callbacks.SURVEY_START
        ))
        keyboard.add(InlineKeyboardButton(
            text="😞 Нет",
            callback_data="bonus:followup_no"
        ))

        await callback.message.answer(
            follow_up_text,
            reply_markup=keyboard.as_markup()
        )
        logger.info("Bonus follow-up sent", user_id=callback.from_user.id)

    except Exception as e:
        logger.error("Error sending bonus file or follow-up", error=str(e), exc_info=True)
        await callback.message.answer("Произошла ошибка. Пожалуйста, попробуйте еще раз позже.")


@router.callback_query(F.data == "bonus:followup_no")
async def handle_bonus_followup_no(
    callback: CallbackQuery,
    state: FSMContext,
    user: Optional[User] = None,
    **kwargs,
):
    """Handle the 'No' response to the bonus follow-up."""

    session = kwargs.get("session")
    conversation_logger = ConversationLoggingService(session) if session else None
    user_id = getattr(user, "id", None) or getattr(callback.from_user, "id", None)

    try:
        await callback.answer()

        response_text = (
            "Понял тебя. Даже если сейчас не готов выбирать инструмент, "
            "можем просто обсудить общие стратегии — это поможет, когда придёт время. "
            "Что для тебя сейчас важнее: надёжность или возможность роста?"
        )

        message = callback.message
        if conversation_logger and message is not None:
            await conversation_logger.send_or_edit(
                message,
                text=response_text,
                user_id=user_id,
                prefer_edit=True,
            )
        elif message is not None:
            await message.edit_text(response_text)

        await state.set_state(DeclinedSurveyStates.waiting_priority_choice)
        logger.info(
            "survey_decline_followup_offered",
            user_id=user_id,
        )

    except Exception as e:  # pragma: no cover - defensive logging
        logger.error(
            "Error in bonus follow-up 'No' handler",
            error=str(e),
            exc_info=True,
            user_id=user_id,
        )
        if callback.message:
            await callback.message.answer("Произошла ошибка.")


@router.message(DeclinedSurveyStates.waiting_priority_choice)
async def handle_strategy_priority_choice(
    message: Message,
    state: FSMContext,
    user: Optional[User] = None,
    **kwargs,
):
    """Process the user's strategic priority after declining the survey."""

    session = kwargs.get("session")
    user_service = UserService(session) if session else None
    target_user = user

    if target_user is None and user_service and message.from_user:
        target_user = await user_service.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )

    conversation_logger = ConversationLoggingService(session) if session else None
    user_id = getattr(target_user, "id", None)

    if conversation_logger and user_id:
        await conversation_logger.log_user_message(
            user_id=user_id,
            text=message.text or "",
        )

    logger.info(
        "strategy_priority_received",
        user_id=user_id,
        text=message.text or "",
    )

    intent = await analysis_service.classify_priority(message.text or "")
    logger.info(
        "strategy_priority_classified",
        user_id=user_id,
        intent=intent.value,
    )

    if intent is PriorityIntent.RELIABILITY:
        if conversation_logger and user_id:
            await conversation_logger.send_or_edit(
                message,
                text=RELIABILITY_MESSAGE,
                user_id=user_id,
                prefer_edit=False,
            )
        else:
            await message.answer(RELIABILITY_MESSAGE)

        await state.set_state(DeclinedSurveyStates.waiting_reliability_confirmation)
        logger.info(
            "strategy_reliability_prompt_sent",
            user_id=user_id,
        )
        return

    if intent is PriorityIntent.GROWTH:
        if conversation_logger and user_id:
            await conversation_logger.send_or_edit(
                message,
                text=GROWTH_MESSAGE,
                user_id=user_id,
                prefer_edit=False,
            )
        else:
            await message.answer(GROWTH_MESSAGE)

        await state.set_state(DeclinedSurveyStates.waiting_growth_confirmation)
        logger.info(
            "strategy_growth_prompt_sent",
            user_id=user_id,
        )
        return

    if conversation_logger and user_id:
        await conversation_logger.send_or_edit(
            message,
            text=CLARIFICATION_MESSAGE,
            user_id=user_id,
            prefer_edit=False,
        )
    else:
        await message.answer(CLARIFICATION_MESSAGE)

    logger.info(
        "strategy_priority_clarification_sent",
        user_id=user_id,
    )


@router.message(DeclinedSurveyStates.waiting_reliability_confirmation)
async def handle_reliability_confirmation(
    message: Message,
    state: FSMContext,
    user: Optional[User] = None,
    **kwargs,
):
    """Handle confirmation when user chooses reliability focus."""

    session = kwargs.get("session")
    user_service = UserService(session) if session else None
    target_user = user

    if target_user is None and user_service and message.from_user:
        target_user = await user_service.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )

    conversation_logger = ConversationLoggingService(session) if session else None
    user_id = getattr(target_user, "id", None)

    if conversation_logger and user_id:
        await conversation_logger.log_user_message(
            user_id=user_id,
            text=message.text or "",
        )

    logger.info(
        "reliability_confirmation_received",
        user_id=user_id,
        text=message.text or "",
    )

    intent = await analysis_service.classify_confirmation(message.text or "")
    logger.info(
        "reliability_confirmation_classified",
        user_id=user_id,
        intent=intent.value,
    )

    if intent is ConfirmationIntent.POSITIVE:
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(
            text="📝 Заполнить заявку",
            callback_data=Callbacks.MANAGER_REQUEST,
        ))

        if conversation_logger and user_id:
            await conversation_logger.send_or_edit(
                message,
                text=APPLICATION_PROMPT_MESSAGE,
                user_id=user_id,
                reply_markup=builder.as_markup(),
                prefer_edit=False,
            )
        else:
            await message.answer(
                APPLICATION_PROMPT_MESSAGE,
                reply_markup=builder.as_markup(),
            )

        await state.clear()
        logger.info(
            "application_form_prompted",
            user_id=user_id,
            focus="reliability",
        )
        return

    if intent is ConfirmationIntent.NEGATIVE:
        if conversation_logger and user_id:
            await conversation_logger.send_or_edit(
                message,
                text=RELIABILITY_DECLINE_MESSAGE,
                user_id=user_id,
                prefer_edit=False,
            )
        else:
            await message.answer(RELIABILITY_DECLINE_MESSAGE)

        await state.clear()
        logger.info(
            "reliability_decline_acknowledged",
            user_id=user_id,
        )
        return

    if conversation_logger and user_id:
        await conversation_logger.send_or_edit(
            message,
            text=CONFIRMATION_CLARIFICATION_MESSAGE,
            user_id=user_id,
            prefer_edit=False,
        )
    else:
        await message.answer(CONFIRMATION_CLARIFICATION_MESSAGE)

    logger.info(
        "reliability_confirmation_clarification_sent",
        user_id=user_id,
    )


@router.message(DeclinedSurveyStates.waiting_growth_confirmation)
async def handle_growth_confirmation(
    message: Message,
    state: FSMContext,
    user: Optional[User] = None,
    **kwargs,
):
    """Handle confirmation when user chooses growth focus."""

    session = kwargs.get("session")
    user_service = UserService(session) if session else None
    target_user = user

    if target_user is None and user_service and message.from_user:
        target_user = await user_service.get_or_create_user(
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
        )

    conversation_logger = ConversationLoggingService(session) if session else None
    user_id = getattr(target_user, "id", None)

    if conversation_logger and user_id:
        await conversation_logger.log_user_message(
            user_id=user_id,
            text=message.text or "",
        )

    logger.info(
        "growth_confirmation_received",
        user_id=user_id,
        text=message.text or "",
    )

    intent = await analysis_service.classify_confirmation(message.text or "")
    logger.info(
        "growth_confirmation_classified",
        user_id=user_id,
        intent=intent.value,
    )

    if intent is ConfirmationIntent.POSITIVE:
        builder = InlineKeyboardBuilder()
        builder.add(InlineKeyboardButton(
            text="📝 Заполнить заявку",
            callback_data=Callbacks.MANAGER_REQUEST,
        ))

        if conversation_logger and user_id:
            await conversation_logger.send_or_edit(
                message,
                text=APPLICATION_PROMPT_MESSAGE,
                user_id=user_id,
                reply_markup=builder.as_markup(),
                prefer_edit=False,
            )
        else:
            await message.answer(
                APPLICATION_PROMPT_MESSAGE,
                reply_markup=builder.as_markup(),
            )

        await state.clear()
        logger.info(
            "application_form_prompted",
            user_id=user_id,
            focus="growth",
        )
        return

    if intent is ConfirmationIntent.NEGATIVE:
        if conversation_logger and user_id:
            await conversation_logger.send_or_edit(
                message,
                text=GROWTH_DECLINE_MESSAGE,
                user_id=user_id,
                prefer_edit=False,
            )
        else:
            await message.answer(GROWTH_DECLINE_MESSAGE)

        await state.clear()
        logger.info(
            "growth_decline_acknowledged",
            user_id=user_id,
        )

        if target_user and user_service and session:
            await start_survey_via_message(
                message,
                session=session,
                user=target_user,
                user_service=user_service,
            )
        return

    if conversation_logger and user_id:
        await conversation_logger.send_or_edit(
            message,
            text=CONFIRMATION_CLARIFICATION_MESSAGE,
            user_id=user_id,
            prefer_edit=False,
        )
    else:
        await message.answer(CONFIRMATION_CLARIFICATION_MESSAGE)

    logger.info(
        "growth_confirmation_clarification_sent",
        user_id=user_id,
    )


def register_handlers(dp):
    """Register start flow handlers."""
    dp.include_router(router)
