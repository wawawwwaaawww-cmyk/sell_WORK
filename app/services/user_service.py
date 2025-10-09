"""User service for business logic operations."""

from typing import Optional, Dict


import structlog
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    User,
    UserSegment,
    FunnelStage,
    Message,
    MessageRole,
    SurveyAnswer,
    Event,
    Lead,
    Appointment,
    Payment,
    UserFunnelState,
    BroadcastDelivery,
)
from app.repositories.user_repository import UserRepository


class UserService:
    """Service for user-related business logic."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = UserRepository(session)
        self.logger = structlog.get_logger()
    
    async def get_or_create_user(
        self,
        telegram_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        source: Optional[str] = None,
    ) -> User:
        """Get existing user or create a new one."""
        
        # Try to get existing user
        user = await self.repository.get_by_telegram_id(telegram_id)
        
        if user:
            # Update user info if provided
            updates = {}
            if username and username != user.username:
                updates["username"] = username
            if first_name and first_name != user.first_name:
                updates["first_name"] = first_name
            if last_name and last_name != user.last_name:
                updates["last_name"] = last_name
            
            if updates:
                user = await self.repository.update(user, **updates)
            
            return user
        
        # Create new user
        return await self.repository.create(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            source=source,
        )
    
    async def calculate_segment_from_score(self, lead_score: int) -> UserSegment:
        """Calculate user segment based on lead score."""
        if lead_score <= 4:
            return UserSegment.COLD
        elif lead_score <= 9:
            return UserSegment.WARM
        else:
            return UserSegment.HOT
    
    async def update_user_segment(self, user: User, lead_score: int) -> User:
        """Update user segment based on lead score."""
        segment = await self.calculate_segment_from_score(lead_score)
        return await self.repository.update_segment(user, segment, lead_score)
    
    async def advance_funnel_stage(self, user: User, stage: FunnelStage) -> User:
        """Advance user to the next funnel stage."""
        return await self.repository.update_funnel_stage(user, stage)
    
    async def set_user_contact_info(
        self,
        user: User,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> User:
        """Set user contact information."""
        return await self.repository.set_contact_info(user, phone, email)
    
    async def is_user_ready_for_consultation(self, user: User) -> bool:
        """Check if user is ready for consultation offer."""
        # Hot segment users are always ready
        if user.segment == UserSegment.HOT:
            return True
        
        # Warm users need to have completed survey
        if user.segment == UserSegment.WARM and user.funnel_stage in [
            FunnelStage.SURVEYED, FunnelStage.ENGAGED, FunnelStage.QUALIFIED
        ]:
            return True
        
        return False
    
    async def is_user_ready_for_payment(self, user: User) -> bool:
        """Check if user is ready for payment offer."""
        return (
            user.segment == UserSegment.HOT and
            user.funnel_stage == FunnelStage.QUALIFIED
        )
    
    async def purge_user_data(self, user: User) -> Dict[str, int]:
        """Remove user and all related records from the database."""
        user_id = getattr(user, "id", None)
        if not user_id:
            return {}

        tables = [
            (SurveyAnswer, "survey_answers"),
            (Event, "events"),
            (Message, "messages"),
            (Lead, "leads"),
            (Appointment, "appointments"),
            (Payment, "payments"),
            (UserFunnelState, "funnel_state"),
            (BroadcastDelivery, "broadcast_deliveries"),
        ]

        stats: Dict[str, int] = {}
        for model, label in tables:
            result = await self.session.execute(
                delete(model).where(model.user_id == user_id)
            )
            rowcount = result.rowcount if result.rowcount is not None else 0
            if rowcount < 0:
                rowcount = 0
            stats[label] = rowcount

        await self.session.delete(user)
        await self.session.flush()
        stats["user"] = 1

        self.logger.info("User data purged", user_id=user_id, stats=stats)
        return stats

    def get_user_display_name(self, user: User) -> str:
        """Get user display name."""
        if user.first_name and user.last_name:
            return f"{user.first_name} {user.last_name}"
        elif user.first_name:
            return user.first_name
        elif user.username:
            return f"@{user.username}"
        else:
            return f"User {user.telegram_id}"
    
    async def get_conversation_history(self, user_id: int, limit: int = 10) -> list:
        """Get recent conversation history for user."""
        try:
            stmt = (
                select(Message)
                .where(Message.user_id == user_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
            )
            result = await self.session.execute(stmt)
            messages = list(result.scalars())

            history = []
            for message_obj in reversed(messages):
                role_value = (
                    message_obj.role.value
                    if isinstance(message_obj.role, MessageRole)
                    else message_obj.role
                )
                history.append(
                    {
                        "role": role_value,
                        "text": message_obj.text,
                        "timestamp": message_obj.created_at,
                        "meta": message_obj.meta or {},
                    }
                )

            return history

        except Exception as e:
            self.logger.error("Error getting conversation history", error=str(e))
            return []
    
    async def save_message(
        self,
        user_id: int,
        role: str,
        text: str,
        metadata: Optional[dict] = None
    ) -> bool:
        """Persist message to conversation history."""
        try:
            try:
                role_enum = MessageRole(role)
            except ValueError:
                role_enum = MessageRole.BOT if role in {"bot", "assistant"} else MessageRole.USER

            message_record = Message(
                user_id=user_id,
                role=role_enum,
                text=text,
                meta=metadata or {},
            )
            self.session.add(message_record)
            await self.session.flush()
            return True

        except Exception as e:
            self.logger.error("Error saving message", error=str(e))
            return False
