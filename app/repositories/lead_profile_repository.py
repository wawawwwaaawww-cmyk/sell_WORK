"""Repository for lead profile persistence."""

from typing import Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LeadProfile


class LeadProfileRepository:
    """Database accessors for lead profiles."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger(__name__)

    async def get_by_user_id(self, user_id: int) -> Optional[LeadProfile]:
        """Fetch lead profile by user id."""
        stmt = select(LeadProfile).where(LeadProfile.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, user_id: int) -> LeadProfile:
        """Create empty lead profile for a user."""
        profile = LeadProfile(user_id=user_id)
        self.session.add(profile)
        await self.session.flush()
        await self.session.refresh(profile)
        self.logger.info("lead_profile_created", user_id=user_id, profile_id=profile.id)
        return profile

    async def save(self, profile: LeadProfile) -> LeadProfile:
        """Persist changes to an existing profile."""
        await self.session.flush()
        await self.session.refresh(profile)
        self.logger.debug("lead_profile_updated", user_id=profile.user_id, profile_id=profile.id)
        return profile
