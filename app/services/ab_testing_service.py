"""A/B testing service for broadcast campaigns."""

import random
import hashlib
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple

import structlog
from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ABTest, ABVariant, ABResult, ABTestStatus, ABTestMetric,
    User, UserSegment, Broadcast
)


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
            test_id=ab_test.id,
            name=name,
            population=population
        )
        
        return ab_test
    
    async def create_variant(
        self,
        ab_test_id: int,
        variant_code: str,
        title: str,
        body: str,
        buttons: Optional[Dict[str, Any]] = None,
        weight: int = 50
    ) -> ABVariant:
        """Create a test variant."""
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
        
        return variant
    
    async def get_ab_test_by_id(self, test_id: int) -> Optional[ABTest]:
        """Get A/B test by ID."""
        stmt = select(ABTest).where(ABTest.id == test_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_running_tests(self) -> List[ABTest]:
        """Get all running A/B tests."""
        stmt = select(ABTest).where(ABTest.status == ABTestStatus.RUNNING)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_test_variants(self, ab_test_id: int) -> List[ABVariant]:
        """Get variants for A/B test."""
        stmt = select(ABVariant).where(ABVariant.ab_test_id == ab_test_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def update_test_status(
        self,
        ab_test: ABTest,
        status: ABTestStatus
    ) -> ABTest:
        """Update A/B test status."""
        ab_test.status = status
        
        await self.session.flush()
        await self.session.refresh(ab_test)
        
        self.logger.info(
            "A/B test status updated",
            test_id=ab_test.id,
            status=status
        )
        
        return ab_test
    
    async def create_or_update_result(
        self,
        ab_test_id: int,
        variant_code: str,
        delivered: int = 0,
        clicks: int = 0,
        conversions: int = 0,
        responses: int = 0,
        unsub: int = 0
    ) -> ABResult:
        """Create or update A/B test result."""
        # Try to get existing result
        stmt = select(ABResult).where(
            and_(
                ABResult.ab_test_id == ab_test_id,
                ABResult.variant_code == variant_code
            )
        )
        result = await self.session.execute(stmt)
        ab_result = result.scalar_one_or_none()
        
        if ab_result:
            # Update existing
            ab_result.delivered += delivered
            ab_result.clicks += clicks
            ab_result.conversions += conversions
            ab_result.responses += responses
            ab_result.unsub += unsub
        else:
            # Create new
            ab_result = ABResult(
                ab_test_id=ab_test_id,
                variant_code=variant_code,
                delivered=delivered,
                clicks=clicks,
                conversions=conversions,
                responses=responses,
                unsub=unsub
            )
            self.session.add(ab_result)
        
        await self.session.flush()
        await self.session.refresh(ab_result)
        
        return ab_result
    
    async def get_test_results(self, ab_test_id: int) -> List[ABResult]:
        """Get results for A/B test."""
        stmt = select(ABResult).where(ABResult.ab_test_id == ab_test_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()


class ABTestingService:
    """Service for A/B testing logic."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = ABTestRepository(session)
        self.logger = structlog.get_logger()
    
    async def create_ab_test(
        self,
        name: str,
        variant_a_title: str,
        variant_a_body: str,
        variant_b_title: str,
        variant_b_body: str,
        population: int = 20,
        metric: ABTestMetric = ABTestMetric.CTR,
        variant_a_buttons: Optional[Dict[str, Any]] = None,
        variant_b_buttons: Optional[Dict[str, Any]] = None
    ) -> ABTest:
        """Create a complete A/B test with two variants."""
        
        # Create A/B test
        ab_test = await self.repository.create_ab_test(name, population, metric)
        
        # Create variant A
        await self.repository.create_variant(
            ab_test_id=ab_test.id,
            variant_code="A",
            title=variant_a_title,
            body=variant_a_body,
            buttons=variant_a_buttons,
            weight=50
        )
        
        # Create variant B
        await self.repository.create_variant(
            ab_test_id=ab_test.id,
            variant_code="B", 
            title=variant_b_title,
            body=variant_b_body,
            buttons=variant_b_buttons,
            weight=50
        )
        
        self.logger.info(
            "A/B test created with variants",
            test_id=ab_test.id,
            name=name
        )
        
        return ab_test
    
    async def start_ab_test(self, test_id: int) -> Tuple[bool, str]:
        """Start an A/B test."""
        try:
            ab_test = await self.repository.get_ab_test_by_id(test_id)
            if not ab_test:
                return False, "A/B test not found"

            status_value = ab_test.status if isinstance(ab_test.status, ABTestStatus) else ABTestStatus(ab_test.status)
            if status_value != ABTestStatus.DRAFT:
                return False, f"Test is already {status_value.value}"

            # Check variants exist
            variants = await self.repository.get_test_variants(test_id)
            if len(variants) < 2:
                return False, "Test needs at least 2 variants"
            
            # Start the test
            await self.repository.update_test_status(ab_test, ABTestStatus.RUNNING)
            
            return True, "A/B test started successfully"
            
        except Exception as e:
            self.logger.error("Error starting A/B test", error=str(e), test_id=test_id)
            return False, "Error starting test"
    
    async def get_test_audience(
        self,
        segment_filter: Optional[Dict[str, Any]] = None
    ) -> List[User]:
        """Get users for A/B test based on filters."""
        stmt = select(User).where(User.is_blocked == False)
        
        if segment_filter:
            # Apply segment filters
            if "segments" in segment_filter:
                segments = segment_filter["segments"]
                stmt = stmt.where(User.segment.in_(segments))
            
            if "min_score" in segment_filter:
                stmt = stmt.where(User.lead_score >= segment_filter["min_score"])
            
            if "funnel_stages" in segment_filter:
                stages = segment_filter["funnel_stages"]
                stmt = stmt.where(User.funnel_stage.in_(stages))
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def select_test_users(
        self,
        all_users: List[User],
        population_percent: int
    ) -> List[User]:
        """Select test audience deterministically based on stable hashing."""
        if not all_users:
            return []

        if population_percent >= 100:
            return all_users

        threshold = max(1, min(100, population_percent))
        selected: List[User] = []
        for user in all_users:
            score = self._stable_user_score(user) % 100
            if score < threshold:
                selected.append(user)

        if selected:
            return selected

        # гарантируем хотя бы одного участника при минимальном пороге
        return [all_users[0]]

    async def assign_variant(
        self,
        user: User,
        variants: List[ABVariant]
    ) -> ABVariant:
        """Assign a variant to user using deterministic hashing."""
        if not variants:
            raise ValueError("No variants provided")

        score = self._stable_user_score(user)

        if len(variants) == 2 and variants[0].weight == variants[1].weight:
            return variants[0] if (score % 100) < 50 else variants[1]

        total_weight = sum(max(1, v.weight) for v in variants) or len(variants)
        bucket = score % total_weight
        cumulative = 0
        for variant in variants:
            cumulative += max(1, variant.weight)
            if bucket < cumulative:
                return variant

        return variants[-1]

    def _stable_user_score(self, user: User) -> int:
        """Return deterministic hash value for routing users."""
        payload = str(user.telegram_id or user.id or "")
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        return int(digest, 16)

    async def record_delivery(
        self,
        ab_test_id: int,
        variant_code: str,
        user_id: int
    ) -> None:
        """Record message delivery for A/B test."""
        await self.repository.create_or_update_result(
            ab_test_id=ab_test_id,
            variant_code=variant_code,
            delivered=1
        )
        
        self.logger.debug(
            "A/B test delivery recorded",
            test_id=ab_test_id,
            variant=variant_code,
            user_id=user_id
        )
    
    async def record_click(
        self,
        ab_test_id: int,
        variant_code: str,
        user_id: int
    ) -> None:
        """Record button click for A/B test."""
        await self.repository.create_or_update_result(
            ab_test_id=ab_test_id,
            variant_code=variant_code,
            clicks=1
        )
        
        self.logger.debug(
            "A/B test click recorded",
            test_id=ab_test_id,
            variant=variant_code,
            user_id=user_id
        )
    
    async def record_conversion(
        self,
        ab_test_id: int,
        variant_code: str,
        user_id: int
    ) -> None:
        """Record conversion for A/B test."""
        await self.repository.create_or_update_result(
            ab_test_id=ab_test_id,
            variant_code=variant_code,
            conversions=1
        )
        
        self.logger.info(
            "A/B test conversion recorded",
            test_id=ab_test_id,
            variant=variant_code,
            user_id=user_id
        )
    
    async def analyze_test_results(self, test_id: int) -> Dict[str, Any]:
        """Analyze A/B test results and determine winner."""
        try:
            ab_test = await self.repository.get_ab_test_by_id(test_id)
            if not ab_test:
                return {"error": "Test not found"}
            
            results = await self.repository.get_test_results(test_id)
            if not results:
                return {"error": "No results available"}
            
            metric_value = ab_test.metric.value if isinstance(ab_test.metric, ABTestMetric) else str(ab_test.metric)
            status_value = ab_test.status if isinstance(ab_test.status, ABTestStatus) else ABTestStatus(ab_test.status)
            analysis = {
                "test_id": test_id,
                "test_name": ab_test.name,
                "metric": metric_value,
                "status": status_value.value,
                "variants": {},
                "winner": None,
                "confidence": 0.0
            }
            
            # Calculate metrics for each variant
            for result in results:
                variant_data = {
                    "delivered": result.delivered,
                    "clicks": result.clicks,
                    "conversions": result.conversions,
                    "responses": result.responses,
                    "unsub": result.unsub,
                    "ctr": result.clicks / result.delivered if result.delivered > 0 else 0,
                    "cr": result.conversions / result.delivered if result.delivered > 0 else 0,
                    "response_rate": result.responses / result.delivered if result.delivered > 0 else 0,
                }
                analysis["variants"][result.variant_code] = variant_data
            
            # Determine winner based on test metric
            if len(analysis["variants"]) >= 2:
                winner_code = None
                best_metric = 0
                
                for variant_code, data in analysis["variants"].items():
                    current_metric = ab_test.metric if isinstance(ab_test.metric, ABTestMetric) else ABTestMetric(ab_test.metric)
                    if current_metric == ABTestMetric.CTR:
                        metric_value = data["ctr"]
                    else:
                        metric_value = data["cr"]
                    
                    if metric_value > best_metric:
                        best_metric = metric_value
                        winner_code = variant_code
                
                analysis["winner"] = winner_code
                analysis["confidence"] = self._calculate_confidence(analysis["variants"])
            
            return analysis
            
        except Exception as e:
            self.logger.error("Error analyzing A/B test", error=str(e), test_id=test_id)
            return {"error": "Analysis failed"}
    
    def _calculate_confidence(self, variants: Dict[str, Dict]) -> float:
        """Calculate statistical confidence (simplified)."""
        # This is a simplified confidence calculation
        # In production, you'd want proper statistical significance testing
        
        if len(variants) < 2:
            return 0.0
        
        variant_list = list(variants.values())
        if len(variant_list) < 2:
            return 0.0
        
        # Get sample sizes
        n1 = variant_list[0]["delivered"]
        n2 = variant_list[1]["delivered"]
        
        # Need minimum sample size
        if n1 < 30 or n2 < 30:
            return 0.0
        
        # Simplified confidence based on sample size difference
        sample_diff = abs(n1 - n2) / max(n1, n2)
        base_confidence = min(0.95, 0.5 + (min(n1, n2) / 1000))
        
        return base_confidence * (1 - sample_diff)
    
    async def complete_test(self, test_id: int) -> Tuple[bool, str, Dict[str, Any]]:
        """Complete A/B test and get final results."""
        try:
            ab_test = await self.repository.get_ab_test_by_id(test_id)
            if not ab_test:
                return False, "Test not found", {}

            status_value = ab_test.status if isinstance(ab_test.status, ABTestStatus) else ABTestStatus(ab_test.status)
            if status_value != ABTestStatus.RUNNING:
                return False, f"Test is not running (status: {status_value.value})", {}

            # Analyze results
            analysis = await self.analyze_test_results(test_id)
            
            # Complete the test
            await self.repository.update_test_status(ab_test, ABTestStatus.COMPLETED)
            
            return True, "Test completed successfully", analysis
            
        except Exception as e:
            self.logger.error("Error completing A/B test", error=str(e), test_id=test_id)
            return False, "Error completing test", {}
    
    async def get_winner_variant(self, test_id: int) -> Optional[ABVariant]:
        """Get winning variant for completed test."""
        try:
            analysis = await self.analyze_test_results(test_id)
            winner_code = analysis.get("winner")
            
            if not winner_code:
                return None
            
            variants = await self.repository.get_test_variants(test_id)
            for variant in variants:
                if variant.variant_code == winner_code:
                    return variant
            
            return None
            
        except Exception as e:
            self.logger.error("Error getting winner variant", error=str(e), test_id=test_id)
            return None
    
    async def should_complete_test(
        self,
        test_id: int,
        min_hours: int = 12,
        min_sample_size: int = 100
    ) -> bool:
        """Check if test should be completed based on time and sample size."""
        try:
            ab_test = await self.repository.get_ab_test_by_id(test_id)
            if not ab_test or ab_test.status != ABTestStatus.RUNNING:
                return False
            
            # Check time elapsed
            time_elapsed = datetime.utcnow() - ab_test.created_at
            if time_elapsed < timedelta(hours=min_hours):
                return False
            
            # Check sample size
            results = await self.repository.get_test_results(test_id)
            total_delivered = sum(r.delivered for r in results)
            
            return total_delivered >= min_sample_size
            
        except Exception as e:
            self.logger.error("Error checking test completion", error=str(e), test_id=test_id)
            return False