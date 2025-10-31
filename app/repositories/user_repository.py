"""User repository for database operations."""

from typing import Optional, List

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, UserSegment, FunnelStage


class UserRepository:
    """Repository for user database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Get user by Telegram ID."""
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        stmt = select(User).where(User.id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Backward-compatible alias for get_by_id."""
        return await self.get_by_id(user_id)

    async def get_by_username(self, username: str) -> Optional[User]:
        """Get user by username (case-insensitive)."""
        stmt = select(User).where(User.username.ilike(username))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def create(
        self,
        telegram_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        source: Optional[str] = None,
    ) -> User:
        """Create a new user."""
        bind = self.session.bind

        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            source=source,
            funnel_stage=FunnelStage.NEW,
        )

        self.session.add(user)
        await self.session.flush()
        await self.session.refresh(user)
        
        self.logger.info(
            "User created",
            user_id=user.id,
            telegram_id=telegram_id,
            username=username,
        )
        
        return user
    
    async def update(
        self,
        user: User,
        **kwargs,
    ) -> User:
        """Update user fields."""
        for key, value in kwargs.items():
            if hasattr(user, key):
                setattr(user, key, value)
        
        await self.session.flush()
        await self.session.refresh(user)
        
        self.logger.info(
            "User updated",
            user_id=user.id,
            fields=list(kwargs.keys()),
        )
        
        return user
    
    async def update_segment(self, user: User, segment: UserSegment, lead_score: int) -> User:
        """Update user segment and lead score."""
        user.segment = segment
        user.lead_score = lead_score
        
        await self.session.flush()
        await self.session.refresh(user)
        
        self.logger.info(
            "User segment updated",
            user_id=user.id,
            segment=segment,
            lead_score=lead_score,
        )
        
        return user
    
    async def update_funnel_stage(self, user: User, stage: FunnelStage) -> User:
        """Update user funnel stage."""
        user.funnel_stage = stage
        
        await self.session.flush()
        await self.session.refresh(user)
        
        self.logger.info(
            "User funnel stage updated",
            user_id=user.id,
            stage=stage,
        )
        
        return user
    
    async def set_contact_info(
        self,
        user: User,
        phone: Optional[str] = None,
        email: Optional[str] = None,
    ) -> User:
        """Set user contact information."""
        if phone:
            user.phone = phone
        if email:
            user.email = email
        
        await self.session.flush()
        await self.session.refresh(user)
        
        self.logger.info(
            "User contact info updated",
            user_id=user.id,
            has_phone=bool(phone),
            has_email=bool(email),
        )
        
        return user
    
    async def block_user(self, user: User) -> User:
        """Block a user."""
        user.is_blocked = True
        
        await self.session.flush()
        await self.session.refresh(user)
        
        self.logger.info("User blocked", user_id=user.id)
        
        return user
    
    async def unblock_user(self, user: User) -> User:
        """Unblock a user."""
        user.is_blocked = False
        
        await self.session.flush()
        await self.session.refresh(user)
        
        self.logger.info("User unblocked", user_id=user.id)
        
        return user
    
    async def get_users_by_segment(self, segment: UserSegment) -> List[User]:
        """Get users by segment."""
        stmt = select(User).where(
            User.segment == segment,
            User.is_blocked == False,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
    
    async def get_active_users_count(self) -> int:
        """Get count of active users."""
        stmt = select(User).where(User.is_blocked == False)
        result = await self.session.execute(stmt)
        return len(result.scalars().all())
