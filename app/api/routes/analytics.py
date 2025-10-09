"""Analytics API routes."""

from typing import Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.services.analytics_formatter import format_report_as_text
from app.services.analytics_service import AnalyticsService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("", summary="Get analytics report")
async def get_analytics_report(
    *,
    days: int = Query(30, ge=1, le=180, description="Количество дней для расчёта отчёта"),
    view: Literal["json", "summary"] = Query(
        "json",
        description="Формат ответа: исходный JSON или текстовое резюме",
    ),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return aggregated analytics metrics for dashboards and bots."""
    service = AnalyticsService(session)

    try:
        report = await service.get_comprehensive_report(days)
    except Exception:
        logger.exception("Failed to generate analytics report", days=days)
        raise HTTPException(status_code=503, detail="Analytics report unavailable")

    if not report:
        raise HTTPException(status_code=503, detail="Analytics report unavailable")

    if view == "summary":
        summary = format_report_as_text(report)
        return {"summary": summary, "period_days": report.get("period_days", days)}

    return report
