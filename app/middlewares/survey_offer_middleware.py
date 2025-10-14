"""Middleware to handle survey offer logic."""

from typing import Callable, Dict, Any, Awaitable

import structlog
from aiogram import BaseMiddleware
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.survey_offer_service import SurveyOfferService
from app.services.user_service import UserService


class SurveyOfferMiddleware(BaseMiddleware):
    """Middleware to check and send survey offers."""

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        
        user: User = data.get("user")
        session: AsyncSession = data.get("session")
        
        if not user or not session or not isinstance(event, Message) or not event.text:
            return await handler(event, data)

        # Ignore commands
        if event.text.startswith("/"):
            return await handler(event, data)

        user_service = UserService(session)
        survey_offer_service = SurveyOfferService(session, user_service)

        # Increment message counter first
        await survey_offer_service.increment_message_counter(user)

        # Then, check if an offer should be made
        await survey_offer_service.check_and_offer_survey(user, event)

        return await handler(event, data)
