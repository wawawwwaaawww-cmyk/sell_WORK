"""Service for managing delayed re-asking of open questions."""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserFunnelState, OpenQuestionLog, OpenQuestionStatus
from app.config import settings
from app.services.scheduler_service import SchedulerService


class ReaskService:
    """
    Manages the lifecycle of open questions, including scheduling and executing re-asks.
    """

    def __init__(self, session: AsyncSession, scheduler: SchedulerService):
        self.session = session
        self.scheduler = scheduler
        self.logger = structlog.get_logger()

    async def set_open_question(
        self,
        user: User,
        funnel_state: UserFunnelState,
        question_id: str,
        text: str,
    ):
        """
        Saves an open question to the user's funnel state and schedules a re-ask.
        """
        if not settings.reask_enabled:
            return

        now = datetime.utcnow()
        reask_due_at = now + timedelta(minutes=settings.reask_first_cooldown_min)

        open_question_data = {
            "question_id": question_id,
            "text": text,
            "asked_at": now.isoformat(),
            "reask_due_at": reask_due_at.isoformat(),
            "attempts": 0,
        }

        if funnel_state.context is None:
            funnel_state.context = {}
        funnel_state.context["open_question"] = open_question_data
        
        # This is a simplified way to mark the object for saving.
        # In a real scenario with SQLAlchemy, this might need specific flagging.
        self.session.add(funnel_state)

        # Schedule the re-ask job
        job_id = f"reask_{user.id}_{question_id}"
        await self.scheduler.schedule_job(
            self.trigger_reask,
            run_date=reask_due_at,
            job_id=job_id,
            kwargs={"user_id": user.id, "question_id": question_id},
        )

        self.logger.info(
            "Open question set and re-ask scheduled",
            user_id=user.id,
            question_id=question_id,
            reask_at=reask_due_at,
        )

    async def close_open_question(
        self,
        funnel_state: UserFunnelState,
        answered: bool = True,
    ):
        """
        Removes the open question from the state and cancels any scheduled jobs.
        """
        if funnel_state.context and "open_question" in funnel_state.context:
            question_id = funnel_state.context["open_question"]["question_id"]
            user_id = funnel_state.user_id
            
            del funnel_state.context["open_question"]
            self.session.add(funnel_state)

            # Cancel the scheduled job
            job_id = f"reask_{user_id}_{question_id}"
            await self.scheduler.cancel_job(job_id)

            self.logger.info(
                "Open question closed",
                user_id=user_id,
                question_id=question_id,
                status="answered" if answered else "skipped",
            )

    async def trigger_reask(self, user_id: int, question_id: str):
        """
        The actual function that gets called by the scheduler to send a re-ask message.
        This is a placeholder for the logic that would send a message to the user.
        """
        # In a real implementation, you would fetch the user and bot instance
        # and send a message.
        self.logger.info(
            "Triggering re-ask for user",
            user_id=user_id,
            question_id=question_id,
        )
        # Example:
        # bot = ...
        # await bot.send_message(chat_id=user_telegram_id, text="Кстати, возвращаясь к нашему вопросу...")