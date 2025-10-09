"""Broadcast repository for managing mass messaging and A/B tests."""

from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Broadcast, ABTest, ABVariant, ABResult, ABTestStatus, ABTestMetric,
    User, UserSegment
)


class BroadcastRepository:
    """Repository for broadcast database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def create_broadcast(
        self,
        title: str,
        body: str,
        buttons: Optional[Dict[str, Any]] = None,
        segment_filter: Optional[Dict[str, Any]] = None
    ) -> Broadcast:
        """Create a new broadcast."""
        broadcast = Broadcast(
            title=title,
            body=body,
            buttons=buttons,
            segment_filter=segment_filter
        )
        
        self.session.add(broadcast)
        await self.session.flush()
        await self.session.refresh(broadcast)
        
        self.logger.info(
            "Broadcast created",
            broadcast_id=broadcast.id,
            title=title
        )
        
        return broadcast
    
    async def get_by_id(self, broadcast_id: int) -> Optional[Broadcast]:
        """Get broadcast by ID."""
        stmt = select(Broadcast).where(Broadcast.id == broadcast_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_recent_broadcasts(self, days: int = 30, limit: int = 20) -> List[Broadcast]:
        """Get recent broadcasts."""
        since_date = datetime.utcnow() - timedelta(days=days)
        
        stmt = select(Broadcast).where(
            Broadcast.created_at >= since_date
        ).order_by(Broadcast.created_at.desc()).limit(limit)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_target_users_for_broadcast(
        self,
        segment_filter: Optional[Dict[str, Any]] = None
    ) -> List[User]:
        """Get users matching broadcast segment filter."""
        stmt = select(User).where(User.is_blocked == False)
        
        if segment_filter:
            # Apply segment filtering
            if "segments" in segment_filter:
                target_segments = segment_filter["segments"]
                stmt = stmt.where(User.segment.in_(target_segments))
            
            if "min_score" in segment_filter:
                stmt = stmt.where(User.lead_score >= segment_filter["min_score"])
            
            if "max_score" in segment_filter:
                stmt = stmt.where(User.lead_score <= segment_filter["max_score"])
            
            if "funnel_stages" in segment_filter:
                target_stages = segment_filter["funnel_stages"]
                stmt = stmt.where(User.funnel_stage.in_(target_stages))
            
            if "exclude_recent_buyers" in segment_filter and segment_filter["exclude_recent_buyers"]:
                # Complex query to exclude users with recent payments
                from app.models import Payment, PaymentStatus
                recent_buyers_subquery = select(Payment.user_id).where(
                    and_(
                        Payment.status == PaymentStatus.PAID,
                        Payment.created_at >= datetime.utcnow() - timedelta(days=30)
                    )
                )
                stmt = stmt.where(User.id.not_in(recent_buyers_subquery))
        
        result = await self.session.execute(stmt)
        return result.scalars().all()


class ABTestRepository:
    """Repository for A/B test database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def create_ab_test(
        self,
        name: str,
        population: int,
        metric: ABTestMetric
    ) -> ABTest:
        """Create a new A/B test."""
        ab_test = ABTest(
            name=name,
            population=population,
            metric=metric,
            status=ABTestStatus.DRAFT
        )
        
        self.session.add(ab_test)
        await self.session.flush()
        await self.session.refresh(ab_test)
        
        self.logger.info(
            "A/B test created",
            ab_test_id=ab_test.id,
            name=name,
            population=population
        )
        
        return ab_test
    
    async def create_ab_variant(
        self,
        ab_test_id: int,
        variant_code: str,
        title: str,
        body: str,
        buttons: Optional[Dict[str, Any]] = None,
        weight: int = 50
    ) -> ABVariant:
        """Create a new A/B test variant."""
        variant = ABVariant(
            ab_test_id=ab_test_id,
            variant_code=variant_code,
            title=title,
            body=body,
            buttons=buttons,
            weight=weight
        )
        
        self.session.add(variant)
        await self.session.flush()
        await self.session.refresh(variant)
        
        self.logger.info(
            "A/B variant created",
            variant_id=variant.id,
            ab_test_id=ab_test_id,
            variant_code=variant_code
        )
        
        return variant
    
    async def get_ab_test_by_id(self, ab_test_id: int) -> Optional[ABTest]:
        """Get A/B test by ID with variants."""
        stmt = select(ABTest).where(ABTest.id == ab_test_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_running_ab_tests(self) -> List[ABTest]:
        """Get all currently running A/B tests."""
        stmt = select(ABTest).where(ABTest.status == ABTestStatus.RUNNING)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def start_ab_test(self, ab_test_id: int) -> bool:
        """Start an A/B test."""
        ab_test = await self.get_ab_test_by_id(ab_test_id)
        if not ab_test or ab_test.status != ABTestStatus.DRAFT:
            return False
        
        ab_test.status = ABTestStatus.RUNNING
        await self.session.flush()
        
        # Initialize results for all variants
        variants_stmt = select(ABVariant).where(ABVariant.ab_test_id == ab_test_id)
        variants_result = await self.session.execute(variants_stmt)
        variants = variants_result.scalars().all()
        
        for variant in variants:
            result = ABResult(
                ab_test_id=ab_test_id,
                variant_code=variant.variant_code
            )
            self.session.add(result)
        
        await self.session.flush()
        
        self.logger.info("A/B test started", ab_test_id=ab_test_id)
        return True
    
    async def stop_ab_test(self, ab_test_id: int) -> bool:
        """Stop an A/B test."""
        ab_test = await self.get_ab_test_by_id(ab_test_id)
        if not ab_test or ab_test.status != ABTestStatus.RUNNING:
            return False
        
        ab_test.status = ABTestStatus.COMPLETED
        await self.session.flush()
        
        self.logger.info("A/B test stopped", ab_test_id=ab_test_id)
        return True
    
    async def record_ab_delivery(
        self,
        ab_test_id: int,
        variant_code: str,
        count: int = 1
    ) -> bool:
        """Record message delivery for A/B test variant."""
        stmt = select(ABResult).where(
            and_(
                ABResult.ab_test_id == ab_test_id,
                ABResult.variant_code == variant_code
            )
        )
        result = await self.session.execute(stmt)
        ab_result = result.scalar_one_or_none()
        
        if ab_result:
            ab_result.delivered += count
            await self.session.flush()
            return True
        
        return False
    
    async def record_ab_click(
        self,
        ab_test_id: int,
        variant_code: str,
        count: int = 1
    ) -> bool:
        """Record button click for A/B test variant."""
        stmt = select(ABResult).where(
            and_(
                ABResult.ab_test_id == ab_test_id,
                ABResult.variant_code == variant_code
            )
        )
        result = await self.session.execute(stmt)
        ab_result = result.scalar_one_or_none()
        
        if ab_result:
            ab_result.clicks += count
            await self.session.flush()
            return True
        
        return False
    
    async def record_ab_conversion(
        self,
        ab_test_id: int,
        variant_code: str,
        count: int = 1
    ) -> bool:
        """Record conversion for A/B test variant."""
        stmt = select(ABResult).where(
            and_(
                ABResult.ab_test_id == ab_test_id,
                ABResult.variant_code == variant_code
            )
        )
        result = await self.session.execute(stmt)
        ab_result = result.scalar_one_or_none()
        
        if ab_result:
            ab_result.conversions += count
            await self.session.flush()
            return True
        
        return False
    
    async def get_ab_test_results(self, ab_test_id: int) -> List[ABResult]:
        """Get results for an A/B test."""
        stmt = select(ABResult).where(ABResult.ab_test_id == ab_test_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def calculate_ab_test_winner(self, ab_test_id: int) -> Optional[str]:
        """Calculate the winning variant for an A/B test."""
        results = await self.get_ab_test_results(ab_test_id)
        if not results:
            return None
        
        ab_test = await self.get_ab_test_by_id(ab_test_id)
        if not ab_test:
            return None
        
        best_variant = None
        best_metric_value = -1.0
        
        for result in results:
            if ab_test.metric == ABTestMetric.CTR:
                # Click-through rate
                metric_value = (result.clicks / max(result.delivered, 1)) * 100
            elif ab_test.metric == ABTestMetric.CR:
                # Conversion rate
                metric_value = (result.conversions / max(result.delivered, 1)) * 100
            else:
                continue
            
            if metric_value > best_metric_value and result.delivered >= 10:  # Minimum sample size
                best_metric_value = metric_value
                best_variant = result.variant_code
        
        return best_variant
    
    async def get_ab_test_analytics(self, ab_test_id: int) -> Dict[str, Any]:
        """Get detailed analytics for an A/B test."""
        ab_test = await self.get_ab_test_by_id(ab_test_id)
        if not ab_test:
            return {}
        
        results = await self.get_ab_test_results(ab_test_id)
        
        analytics = {
            "test_id": ab_test_id,
            "test_name": ab_test.name,
            "status": ab_test.status,
            "metric": ab_test.metric,
            "total_delivered": sum(r.delivered for r in results),
            "total_clicks": sum(r.clicks for r in results),
            "total_conversions": sum(r.conversions for r in results),
            "variants": []
        }
        
        for result in results:
            ctr = (result.clicks / max(result.delivered, 1)) * 100
            cr = (result.conversions / max(result.delivered, 1)) * 100
            
            variant_data = {
                "variant_code": result.variant_code,
                "delivered": result.delivered,
                "clicks": result.clicks,
                "conversions": result.conversions,
                "ctr": round(ctr, 2),
                "cr": round(cr, 2)
            }
            analytics["variants"].append(variant_data)
        
        # Calculate winner
        winner = await self.calculate_ab_test_winner(ab_test_id)
        analytics["winner"] = winner
        
        return analytics
    
    async def select_variant_for_user(
        self,
        ab_test_id: int,
        user_id: int
    ) -> Optional[ABVariant]:
        """Select appropriate variant for a user in an A/B test."""
        
        # Get test variants
        variants_stmt = select(ABVariant).where(ABVariant.ab_test_id == ab_test_id)
        variants_result = await self.session.execute(variants_stmt)
        variants = variants_result.scalars().all()
        
        if not variants:
            return None
        
        # Simple hash-based selection for consistent assignment
        import hashlib
        hash_input = f"{ab_test_id}:{user_id}".encode()
        hash_value = int(hashlib.md5(hash_input).hexdigest()[:8], 16)
        
        # Calculate cumulative weights
        total_weight = sum(v.weight for v in variants)
        selection_point = hash_value % total_weight
        
        cumulative_weight = 0
        for variant in variants:
            cumulative_weight += variant.weight
            if selection_point < cumulative_weight:
                return variant
        
        # Fallback to first variant
        return variants[0] if variants else None

    async def mark_as_sent(self, broadcast_id: int, user_id: int) -> None:
        """Mark broadcast as sent to specific user."""
        try:
            # Create or update broadcast delivery record
            from app.models import BroadcastDelivery
            
            delivery = BroadcastDelivery(
                broadcast_id=broadcast_id,
                user_id=user_id,
                status="sent",
                sent_at=datetime.utcnow()
            )
            
            self.session.add(delivery)
            await self.session.flush()
            
        except Exception as e:
            self.logger.error(f"Error marking broadcast as sent: {e}")

    async def mark_as_failed(self, broadcast_id: int, user_id: int) -> None:
        """Mark broadcast as failed for specific user."""
        try:
            from app.models import BroadcastDelivery
            
            delivery = BroadcastDelivery(
                broadcast_id=broadcast_id,
                user_id=user_id,
                status="failed",
                failed_at=datetime.utcnow()
            )
            
            self.session.add(delivery)
            await self.session.flush()
            
        except Exception as e:
            self.logger.error(f"Error marking broadcast as failed: {e}")