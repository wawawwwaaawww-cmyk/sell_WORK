"""Service for managing survey offers."""

from datetime import datetime, timedelta
from typing import Optional
import json

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.types import InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import User
from app.config import settings
from app.services.llm_service import LLMService, LLMContext
from app.services.user_service import UserService
from app.services.event_service import EventService
from app.utils.callbacks import Callbacks


class SurveyOfferService:
    """Manages the logic for offering surveys to users who skipped them."""

    def __init__(self, session: AsyncSession, user_service: UserService):
        self.session = session
        self.user_service = user_service
        self.logger = structlog.get_logger()
        self.llm_service = LLMService()
        self.event_service = EventService(session)

    async def check_and_offer_survey(self, user: User, message: Message):
        """Check if a survey offer should be made and send it."""
        if not settings.survey_offer_enabled or user.survey_completed or not user.survey_skipped_at:
            return

        now = datetime.utcnow()
        if user.survey_offer_snooze_until and now < user.survey_offer_snooze_until:
            return

        if user.last_offer_at and (now - user.last_offer_at) < timedelta(minutes=settings.survey_offer_min_interval_min):
            return

        is_first_offer = user.offer_attempt == 0
        trigger_first = is_first_offer and user.msgs_since_skip >= settings.survey_offer_first_after_msgs
        trigger_repeat = not is_first_offer and user.msgs_since_skip > 0 and (user.msgs_since_skip % settings.survey_offer_repeat_every_msgs == 0)

        if not (trigger_first or trigger_repeat):
            return

        # TODO: Add check for active flows if required by settings.survey_offer_skip_during_active_flows

        offer_data = await self._generate_offer_text(user)
        await self._send_offer(user, message, offer_data)

    async def _generate_offer_text(self, user: User) -> dict:
        """Generate the survey offer text using LLM with a fallback."""
        prompt = """
        Generate a short, friendly, and compelling message (1-2 sentences) to encourage a user to complete a survey.
        The message should highlight a benefit for the user and have a clear call to action.
        Vary the arguments, focusing on one of these benefits:
        - Personalized offer/program selection.
        - Saving time/money.
        - Matching with the right expert/mentor.
        - Getting a bonus/relevant materials.
        - Receiving a clear action plan.
        
        Style requirements:
        - Friendly, no income promises or guarantees.
        - The user has previously skipped the survey.
        
        Format your response as a JSON object with "text" and "buttons" keys.
        Example:
        {
          "text": "Кстати, если уделите пару минут анкете, я смогу подобрать для вас персональный план. Начнем?",
          "buttons": ["Пройти анкету", "Позже"]
        }
        """
        try:
            context = LLMContext(user=user, messages_history=[{"role": "system", "text": prompt}])
            response = await self.llm_service.generate_response(context, model=settings.survey_offer_model, temperature=0.8)
            parsed_response = json.loads(response.reply_text)
            if "text" in parsed_response and "buttons" in parsed_response:
                return parsed_response
        except Exception as e:
            self.logger.error("LLM generation for survey offer failed", error=str(e), user_id=user.id)

        return {
            "text": "Короткая анкета на 1–2 минуты поможет понять, что вам интереснее, и предложить лучший вариант. Пройдём сейчас?",
            "buttons": ["Пройти анкету", "Позже"]
        }

    async def _send_offer(self, user: User, message: Message, offer_data: dict):
        """Send the survey offer message to the user."""
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text=offer_data["buttons"][0], callback_data=Callbacks.SURVEY_START_FROM_OFFER))
        keyboard.add(InlineKeyboardButton(text=offer_data["buttons"][1], callback_data=Callbacks.SURVEY_OFFER_LATER))
        # Optional snooze button
        # keyboard.add(InlineKeyboardButton(text="Не предлагать неделю", callback_data=Callbacks.SURVEY_OFFER_SNOOZE))
        keyboard.adjust(1)

        await message.answer(offer_data["text"], reply_markup=keyboard.as_markup())

        now = datetime.utcnow()
        user.last_offer_at = now
        user.offer_attempt += 1
        user.msgs_since_skip = 0  # Reset counter after offer
        await self.session.commit()

        await self.event_service.log_event(
            user_id=user.id,
            event_type="survey_invite_shown",
            payload={
                "attempt": user.offer_attempt,
                "reason": "after_5" if user.offer_attempt == 1 else "after_6n",
                "text": offer_data["text"],
            },
        )
        self.logger.info("Sent survey offer", user_id=user.id, attempt=user.offer_attempt)

    async def increment_message_counter(self, user: User):
        """Increment the message counter for a user who skipped the survey."""
        if user.survey_completed or not user.survey_skipped_at:
            return

        user.msgs_since_skip += 1
        await self.session.commit()
        self.logger.info(
            "Incremented msgs_since_skip",
            user_id=user.id,
            new_count=user.msgs_since_skip,
        )

    async def mark_survey_skipped(self, user: User):
        """Mark that the user has skipped the survey."""
        if user.survey_completed:
            return

        now = datetime.utcnow()
        user.survey_skipped_at = now
        user.msgs_since_skip = 0
        await self.session.commit()
        await self.event_service.log_event(
            user_id=user.id,
            event_type="survey_skipped",
            payload={"timestamp": now.isoformat()},
        )
        self.logger.info("Marked survey as skipped", user_id=user.id)

    async def mark_survey_completed(self, user: User):
        """Mark the survey as completed and reset offer-related fields."""
        now = datetime.utcnow()
        user.survey_completed = True
        user.survey_completed_at = now
        user.msgs_since_skip = 0
        user.offer_attempt = 0
        user.last_offer_at = None
        user.survey_skipped_at = None
        user.survey_offer_snooze_until = None
        
        await self.session.commit()
        await self.event_service.log_event(
            user_id=user.id,
            event_type="survey_completed_from_offer",
            payload={"timestamp": now.isoformat()},
        )
        self.logger.info("Marked survey as completed, reset offer fields", user_id=user.id)
