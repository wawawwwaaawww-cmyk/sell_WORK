"""Repository for product matching audit log."""

from __future__ import annotations

from typing import Optional, Dict, Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProductMatchLog


class ProductMatchLogRepository:
    """Persist and query product matching audit trail."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()

    async def log_match(
        self,
        *,
        user_id: int,
        product_id: Optional[int],
        score: float,
        top3: Dict[str, Any],
        explanation: Optional[str],
        threshold: Optional[float],
        trigger: Optional[str] = None,
    ) -> ProductMatchLog:
        """Persist a product match decision."""
        entry = ProductMatchLog(
            user_id=user_id,
            product_id=product_id,
            score=score,
            top3=top3,
            explanation=explanation,
            threshold_used=threshold,
            trigger=trigger,
        )
        self.session.add(entry)
        await self.session.flush()
        await self.session.refresh(entry)

        self.logger.info(
            "Product match logged",
            user_id=user_id,
            product_id=product_id,
            score=score,
            trigger=trigger,
        )

        return entry

    async def get_recent_for_user(self, user_id: int, limit: int = 5) -> list[ProductMatchLog]:
        """Fetch recent match log entries for diagnostics."""
        stmt = (
            select(ProductMatchLog)
            .where(ProductMatchLog.user_id == user_id)
            .order_by(ProductMatchLog.matched_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

