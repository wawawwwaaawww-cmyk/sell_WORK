"""Analytics service for metrics collection and reporting."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from sqlalchemy import select, func, and_
from sqlalchemy.exc import ProgrammingError, OperationalError
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    User,
    Lead,
    Event,
    Payment,
    Broadcast,
    BroadcastDelivery,
    ABTest,
    ABVariant,
    ABResult,
    PaymentStatus,
    ABTestStatus,
    ABTestMetric,
)
from app.services.ab_testing_service import ABTestingService

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Service for analytics and reporting."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_user_metrics(self, days: int = 30) -> Dict:
        """Get user-related metrics."""
        try:
            now_utc = datetime.now(timezone.utc)
            period_start = now_utc - timedelta(days=days)
            active_threshold = now_utc - timedelta(days=7)

            total_users = await self.db.scalar(select(func.count(User.id))) or 0
            new_users = await self.db.scalar(
                select(func.count(User.id)).where(User.created_at >= period_start)
            ) or 0
            active_users = await self.db.scalar(
                select(func.count(User.id)).where(User.updated_at >= active_threshold)
            ) or 0

            segments_result = await self.db.execute(
                select(User.segment, func.count(User.id)).group_by(User.segment)
            )
            segments: Dict[str, int] = {}
            for segment, count in segments_result.all():
                segments[str(segment or "unknown")] = count

            return {
                "total_users": total_users,
                "new_users": new_users,
                "active_users": active_users,
                "segments": segments,
            }

        except Exception as exc:
            logger.error("Error getting user metrics", exc_info=exc)
            return {}

    async def get_lead_metrics(self, days: int = 30) -> Dict:
        """Get lead-related metrics."""
        try:
            period_start = datetime.now(timezone.utc) - timedelta(days=days)

            total_leads = await self.db.scalar(select(func.count(Lead.id))) or 0
            new_leads = await self.db.scalar(
                select(func.count(Lead.id)).where(Lead.created_at >= period_start)
            ) or 0

            status_result = await self.db.execute(
                select(Lead.status, func.count(Lead.id))
                .where(Lead.created_at >= period_start)
                .group_by(Lead.status)
            )
            lead_statuses: Dict[str, int] = {}
            for status, count in status_result.all():
                lead_statuses[str(status)] = count

            return {
                "total_leads": total_leads,
                "new_leads": new_leads,
                "lead_statuses": lead_statuses,
            }

        except Exception as exc:
            logger.error("Error getting lead metrics", exc_info=exc)
            return {}

    async def get_sales_metrics(self, days: int = 30) -> Dict:
        """Get sales-related metrics."""
        try:
            period_start = datetime.now(timezone.utc) - timedelta(days=days)

            paid_filter = and_(
                Payment.status == PaymentStatus.PAID,
                Payment.created_at >= period_start,
            )

            total_revenue = await self.db.scalar(
                select(func.sum(Payment.amount)).where(paid_filter)
            ) or 0

            successful_payments = await self.db.scalar(
                select(func.count(Payment.id)).where(paid_filter)
            ) or 0

            avg_order_value = (
                float(total_revenue) / successful_payments if successful_payments else 0.0
            )

            return {
                "total_revenue": float(total_revenue),
                "successful_payments": successful_payments,
                "avg_order_value": round(avg_order_value, 2),
            }

        except Exception as exc:
            logger.error("Error getting sales metrics", exc_info=exc)
            return {}


    async def get_broadcast_metrics(self, days: int = 30) -> Dict[str, Any]:
        """Get broadcast and delivery metrics."""
        try:
            period_start = datetime.now(timezone.utc) - timedelta(days=days)

            total_broadcasts = await self.db.scalar(select(func.count(Broadcast.id))) or 0
            recent_broadcasts = await self.db.scalar(
                select(func.count(Broadcast.id)).where(Broadcast.created_at >= period_start)
            ) or 0

            delivery_stmt = (
                select(BroadcastDelivery.status, func.count(BroadcastDelivery.id))
                .where(BroadcastDelivery.created_at >= period_start)
                .group_by(BroadcastDelivery.status)
            )
            delivery_counts = {
                status or "unknown": count
                for status, count in (await self.db.execute(delivery_stmt)).all()
            }

            sent = delivery_counts.get("sent", 0)
            failed = delivery_counts.get("failed", 0)
            pending = delivery_counts.get("pending", 0)
            total_deliveries = sent + failed + pending
            failure_rate = round(failed / total_deliveries, 4) if total_deliveries else 0.0

            unique_recipients = await self.db.scalar(
                select(func.count(func.distinct(BroadcastDelivery.user_id))).where(
                    BroadcastDelivery.created_at >= period_start
                )
            ) or 0

            avg_reach = round(sent / recent_broadcasts, 2) if recent_broadcasts else 0.0

            latest_stmt = (
                select(Broadcast.title, Broadcast.created_at)
                .order_by(Broadcast.created_at.desc())
                .limit(1)
            )
            latest_row = (await self.db.execute(latest_stmt)).first()
            latest = None
            if latest_row:
                title, created_at = latest_row
                latest = {
                    "title": title,
                    "created_at": created_at.isoformat() if created_at else None,
                }

            return {
                "total_broadcasts": total_broadcasts,
                "broadcasts_last_period": recent_broadcasts,
                "deliveries": {
                    "total": total_deliveries,
                    "sent": sent,
                    "failed": failed,
                    "pending": pending,
                    "failure_rate": failure_rate,
                    "unique_recipients": unique_recipients,
                    "avg_recipients_per_broadcast": avg_reach,
                },
                "latest": latest,
            }

        except Exception as exc:
            logger.error("Error getting broadcast metrics", exc_info=exc)
            return {}

    async def get_ab_test_metrics(self, days: int = 30) -> Dict[str, Any]:
        """Get A/B test performance snapshot."""
        try:
            period_start = datetime.now(timezone.utc) - timedelta(days=days)

            tests_stmt = (
                select(ABTest)
                .options(selectinload(ABTest.variants), selectinload(ABTest.results))
                .where(ABTest.created_at >= period_start)
            )
            tests = (await self.db.execute(tests_stmt)).scalars().unique().all()

            ab_service = ABTestingService(self.db)

            summary = {
                "total": len(tests),
                "running": sum(
                    1
                    for test in tests
                    if (
                        test.status.value
                        if isinstance(test.status, ABTestStatus)
                        else str(test.status)
                    )
                    == ABTestStatus.RUNNING.value
                ),
                "completed": sum(
                    1
                    for test in tests
                    if (
                        test.status.value
                        if isinstance(test.status, ABTestStatus)
                        else str(test.status)
                    )
                    == ABTestStatus.COMPLETED.value
                ),
            }

            tests_payload = []
            for test in tests:
                analysis = await ab_service.analyze_test_results(test.id)
                if analysis.get("error") == "ab_tables_missing":
                    return {
                        "summary": summary,
                        "tests": [],
                        "error": "ab_tables_missing",
                    }
                if analysis.get("error"):
                    continue
                metric_raw = analysis.get("metric") or (
                    test.metric.value if isinstance(test.metric, ABTestMetric) else str(test.metric)
                )
                status_value = analysis.get("status") or (
                    test.status.value if isinstance(test.status, ABTestStatus) else str(test.status)
                )

                variants_payload = []
                for variant in analysis.get("variants", []):
                    variants_payload.append(
                        {
                            "variant": variant.get("variant"),
                            "delivered": variant.get("delivered", 0),
                            "clicks": variant.get("unique_clicks", 0),
                            "leads": variant.get("leads", 0),
                            "responses": variant.get("responses", 0),
                            "unsub": variant.get("unsubscribed", 0),
                            "blocked": variant.get("blocked", 0),
                            "ctr": variant.get("ctr", 0.0),
                            "cr": variant.get("cr", 0.0),
                            "unsub_rate": variant.get("unsub_rate", 0.0),
                        }
                    )

                winner = analysis.get("winner")

                tests_payload.append(
                    {
                        "id": test.id,
                        "name": analysis.get("name") or test.name,
                        "metric": metric_raw,
                        "status": status_value,
                        "population": test.population,
                        "audience_size": analysis.get("audience_size"),
                        "test_size": analysis.get("test_size"),
                        "started_at": analysis.get("started_at"),
                        "finished_at": analysis.get("finished_at"),
                        "variants": variants_payload,
                        "winner": winner,
                    }
                )

            return {
                "summary": summary,
                "tests": tests_payload,
            }

        except (ProgrammingError, OperationalError) as exc:
            logger.warning("A/B tables missing while preparing metrics", exc_info=exc)
            return {"summary": {}, "tests": [], "error": "ab_tables_missing", "detail": str(exc)}
        except Exception as exc:
            logger.error("Error getting A/B test metrics", exc_info=exc)
            return {"summary": {}, "tests": [], "error": "ab_query_failed", "detail": str(exc)}

    async def get_comprehensive_report(self, days: int = 30) -> Dict[str, Any]:
        """Get comprehensive analytics report."""
        try:
            user_metrics = await self.get_user_metrics(days)
            lead_metrics = await self.get_lead_metrics(days)
            sales_metrics = await self.get_sales_metrics(days)
            broadcast_metrics = await self.get_broadcast_metrics(days)
            ab_test_metrics = await self.get_ab_test_metrics(days)

            return {
                "period_days": days,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "users": user_metrics,
                "leads": lead_metrics,
                "sales": sales_metrics,
                "broadcasts": broadcast_metrics,
                "ab_tests": ab_test_metrics,
            }

        except Exception as exc:
            logger.error("Error generating comprehensive report", exc_info=exc)
            return {}
