"""Middleware to handle the re-ask logic for unanswered questions."""

from typing import Callable, Dict, Any, Awaitable
import asyncio

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message

from app.services.intent_router_service import IntentRouterService, UserIntent
from app.services.reask_service import ReaskService
from app.services.user_service import UserService
from app.models import UserFunnelState


class ReaskMiddleware(BaseMiddleware):
    """
    This middleware intercepts messages to implement the "answer-first" logic.
    """

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        
        session = data.get("session")
        user = data.get("user")
        if not session or not user or not isinstance(event, Message) or not event.text:
            return await handler(event, data)

        logger = structlog.get_logger()
        intent_router = IntentRouterService(session)
        
        # We need a way to get the message history here.
        # This is a placeholder for that logic.
        message_history = [] 

        intent_response = await intent_router.classify_intent(user, event.text, message_history)

        if intent_response.intent == UserIntent.ANSWER_TO_QUESTION:
            # If the user answered the question, we need to find the funnel state
            # and close the open question.
            user_service = UserService(session)
            funnel_state = await user_service.get_user_funnel_state(user.id)
            if funnel_state and funnel_state.context and "open_question" in funnel_state.context:
                reask_service = ReaskService(session, data["scheduler"])
                await reask_service.close_open_question(funnel_state)
            
            return await handler(event, data)
        
        # If the intent is different, we handle it here and potentially don't call the next handler.
        logger.info(
            "User changed topic, handling intent without asking again.",
            user_id=user.id,
            intent=intent_response.intent.value,
        )

        # Here we would implement the logic from the TZ:
        # 1. Save the original question as an "open_question" in the user's state.
        # 2. Respond to the user's immediate intent (e.g., start sales flow).
        # 3. The ReaskService would have already scheduled a follow-up.

        # For now, we will just log it and pass the event to the next handler
        # to avoid breaking existing flows until the full logic is implemented.
        # In the final version, we might stop propagation here.
        
        # Example of how it might look:
        # if intent_response.intent == UserIntent.BUY_NOW:
        #     await event.answer("Вижу, вы хотите купить! Минутку, сейчас расскажу про наши курсы...")
        #     # Don't call handler(event, data) to override the normal flow
        #     return

        return await handler(event, data)