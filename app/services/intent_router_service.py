"""Service to classify user intent based on message text."""

from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm_service import LLMService, LLMContext
from app.models import User


class UserIntent(str, Enum):
    """Enumeration of possible user intents."""
    BUY_NOW = "buy_now"
    BOOK_CONSULT = "book_consult"
    PAY = "pay"
    ASK_PRICE = "ask_price"
    OBJECTION = "objection"
    SMALLTALK = "smalltalk"
    OFF_TOPIC = "off_topic"
    ABUSIVE = "abusive"
    ANSWER_TO_QUESTION = "answer_to_question"
    UNKNOWN = "unknown"


@dataclass
class IntentResponse:
    """Structured response from the intent classification."""
    intent: UserIntent
    answer: Optional[str] = None
    next_action: Optional[str] = None
    stage_transition: Optional[str] = None
    need_reask: bool = False
    raw_response: Optional[Dict[str, Any]] = None


class IntentRouterService:
    """
    Service to route user messages based on classified intent.
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
        self.llm_service = LLMService(session)

    async def classify_intent(self, user: User, message_text: str, history: List[Dict[str, str]]) -> IntentResponse:
        """
        Classifies the user's intent using an LLM call.
        """
        self.logger.info("Starting intent classification", user_id=user.id)

        # This is a placeholder for the actual LLM call logic.
        # In a real implementation, this would involve a specially crafted prompt.
        # For now, we'll simulate the logic.

        # Simulate LLM response based on keywords for now
        text_lower = message_text.lower()
        if "купить" in text_lower or "продай" in text_lower:
            intent = UserIntent.BUY_NOW
        elif "консультация" in text_lower or "записаться" in text_lower:
            intent = UserIntent.BOOK_CONSULT
        elif "цена" in text_lower or "сколько стоит" in text_lower:
            intent = UserIntent.ASK_PRICE
        elif "дорого" in text_lower or "не уверен" in text_lower:
            intent = UserIntent.OBJECTION
        elif "привет" in text_lower or "как дела" in text_lower:
            intent = UserIntent.SMALLTALK
        else:
            intent = UserIntent.ANSWER_TO_QUESTION

        self.logger.info(
            "Intent classified",
            user_id=user.id,
            intent=intent.value,
        )

        return IntentResponse(
            intent=intent,
            answer="This is a simulated answer.",
            need_reask=False  # This will be determined by the LLM in the real implementation
        )