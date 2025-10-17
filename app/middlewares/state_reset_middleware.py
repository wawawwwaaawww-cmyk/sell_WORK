"""Middleware for resetting user state on command or callback."""

from typing import Any, Awaitable, Callable, Dict

import structlog
from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, TelegramObject

from app.models import User
from app.services.scheduler_service import scheduler_service

logger = structlog.get_logger()


class StateResetMiddleware(BaseMiddleware):
    """
    This middleware resets the user's FSM state whenever a command or a system callback is received.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        """
        Handle the incoming event.
        """
        is_command = isinstance(event, Message) and event.text and event.text.startswith("/")
        is_callback = isinstance(event, CallbackQuery)
        callback_data = event.data if is_callback else ""
        
        # Do not reset state for consultation flow callbacks
        if is_callback and event.data and event.data.startswith("consult_"):
            return await handler(event, data)

        if is_command or is_callback:
            state: FSMContext = data.get("state")
            user: User = data.get("user")

            if state:
                current_state = await state.get_state()
                if current_state is not None:
                    # Preserve stateful admin/application flows handled via callbacks
                    if is_callback and current_state.startswith(
                        (
                            "AdminStates:",
                            "AdminEnhancedStates:",
                            "ApplicationStates:",
                            "ConsultationStates:",
                            "DeclinedSurveyStates:",
                        )
                    ):
                        return await handler(event, data)

                    # Skip reset for explicitly whitelisted callbacks
                    if (
                        is_callback
                        and callback_data
                        and callback_data.startswith(("admin_", "product_", "leads_", "bonus:", "bonus_"))
                    ):
                        return await handler(event, data)

                    # Cancel any scheduled scene-specific jobs
                    state_data = await state.get_data()

                    # Invalidate previous inline keyboards
                    last_message_id = state_data.get("last_message_with_keyboard_id")
                    if last_message_id and isinstance(event, (Message, CallbackQuery)):
                        chat_id = event.chat.id if isinstance(event, Message) else event.message.chat.id
                        try:
                            await data["bot"].edit_message_reply_markup(
                                chat_id=chat_id,
                                message_id=last_message_id,
                                reply_markup=None
                            )
                            logger.info(
                                "Invalidated keyboard for message",
                                message_id=last_message_id,
                                user_id=user.id if user else "unknown",
                            )
                        except Exception as e:
                            logger.warning(
                                "Failed to invalidate keyboard",
                                message_id=last_message_id,
                                user_id=user.id if user else "unknown",
                                error=str(e),
                            )
                    
                    scene_job_id = state_data.get("scene_job_id")
                    if scene_job_id:
                        scheduler_service.cancel_job(scene_job_id)
                        logger.info(
                            "Cancelled scene job",
                            job_id=scene_job_id,
                            user_id=user.id if user else "unknown",
                        )

                    # Perform state reset
                    await state.clear()

                    # Log the reset event
                    cause = "cmd" if is_command else "callback"
                    logger.info(
                        "RESET cause=%s prev_state=%s",
                        cause,
                        current_state,
                        user_id=user.id if user else "unknown",
                    )

        return await handler(event, data)
