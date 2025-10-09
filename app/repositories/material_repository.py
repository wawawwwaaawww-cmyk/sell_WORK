
"""Material repository for managing marketing content catalogue."""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

import structlog
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Material,
    MaterialType,
    MaterialStatus,
    MaterialContentType,
    MaterialTag,
    MaterialSegment,
    MaterialStage,
    MaterialVersion,
    MaterialMetric,
    UserSegment,
)


class MaterialRepository:
    """Repository encapsulating DB access for marketing materials."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()

    # ------------------------------------------------------------------
    # Helper builders
    # ------------------------------------------------------------------

    def _base_query(self):
        """Return base select for materials with eager loading."""
        return (
            select(Material)
            .where(Material.status == MaterialStatus.READY)
            .options(
                selectinload(Material.versions),
                selectinload(Material.tags_rel),
                selectinload(Material.segments_rel),
                selectinload(Material.stages_rel),
            )
        )

    async def _execute_materials(self, stmt) -> List[Material]:
        result = await self.session.execute(stmt)
        return result.scalars().unique().all()

    # ------------------------------------------------------------------
    # CRUD / retrieval methods
    # ------------------------------------------------------------------

    async def get_by_id(self, material_id: str) -> Optional[Material]:
        stmt = self._base_query().where(Material.id == material_id)
        materials = await self._execute_materials(stmt)
        return materials[0] if materials else None

    async def get_recent_materials(self, limit: int = 20) -> List[Material]:
        stmt = self._base_query().order_by(Material.updated_at.desc()).limit(limit)
        return await self._execute_materials(stmt)

    async def get_all_materials(self, limit: int = 50) -> List[Material]:
        stmt = self._base_query().order_by(Material.priority.desc(), Material.updated_at.desc()).limit(limit)
        return await self._execute_materials(stmt)

    async def get_materials_by_segment(
        self,
        segment: UserSegment,
        limit: int = 10,
        stage: Optional[str] = None,
    ) -> List[Material]:
        stmt = self._base_query().join(MaterialSegment, MaterialSegment.material_id == Material.id)
        stmt = stmt.where(MaterialSegment.segment == segment.value)
        if stage:
            stmt = stmt.join(MaterialStage, MaterialStage.material_id == Material.id).where(MaterialStage.stage == stage)
        stmt = stmt.order_by(Material.priority.desc(), Material.updated_at.desc()).limit(limit)
        return await self._execute_materials(stmt)

    async def get_materials_by_tags(
        self,
        tags: Sequence[str],
        limit: int = 10,
    ) -> List[Material]:
        if not tags:
            return []
        lowered = [tag.lower() for tag in tags]
        stmt = (
            self._base_query()
            .join(MaterialTag, MaterialTag.material_id == Material.id)
            .where(MaterialTag.tag.in_(lowered))
            .order_by(Material.priority.desc(), Material.updated_at.desc())
            .limit(limit)
        )
        return await self._execute_materials(stmt)

    async def get_materials_by_type(
        self,
        material_type: MaterialType,
        limit: int = 10,
    ) -> List[Material]:
        stmt = (
            self._base_query()
            .where(Material.category == material_type.value)
            .order_by(Material.priority.desc(), Material.updated_at.desc())
            .limit(limit)
        )
        return await self._execute_materials(stmt)

    async def get_materials_by_content_type(
        self,
        content_type: MaterialContentType,
        limit: int = 10,
    ) -> List[Material]:
        stmt = (
            self._base_query()
            .where(Material.content_type == content_type.value)
            .order_by(Material.priority.desc(), Material.updated_at.desc())
            .limit(limit)
        )
        return await self._execute_materials(stmt)

    async def search_materials(self, query: str, limit: int = 10) -> List[Material]:
        if not query:
            return []
        pattern = f"%{query.lower()}%"
        stmt = (
            self._base_query()
            .join(MaterialVersion, MaterialVersion.material_id == Material.id)
            .where(
                MaterialVersion.is_active == True,
                or_(
                    func.lower(Material.title).like(pattern),
                    func.lower(func.coalesce(Material.summary, "")).like(pattern),
                    func.lower(func.coalesce(MaterialVersion.extracted_text, "")).like(pattern),
                ),
            )
            .order_by(Material.priority.desc(), Material.updated_at.desc())
            .limit(limit)
        )
        return await self._execute_materials(stmt)

    async def get_materials_for_newbies(self, limit: int = 5) -> List[Material]:
        newbie_tags = ["новичкам", "основы", "начинающим", "базовые", "простые"]
        stmt = (
            self._base_query()
            .join(MaterialTag, MaterialTag.material_id == Material.id)
            .where(MaterialTag.tag.in_(newbie_tags))
            .order_by(Material.priority.desc(), Material.updated_at.desc())
            .limit(limit)
        )
        return await self._execute_materials(stmt)

    async def get_materials_for_traders(self, limit: int = 5) -> List[Material]:
        trader_tags = ["трейдинг", "торговля", "анализ", "стратегии", "технический"]
        stmt = (
            self._base_query()
            .join(MaterialTag, MaterialTag.material_id == Material.id)
            .where(MaterialTag.tag.in_(trader_tags))
            .order_by(Material.priority.desc(), Material.updated_at.desc())
            .limit(limit)
        )
        return await self._execute_materials(stmt)

    async def get_materials_for_investors(self, limit: int = 5) -> List[Material]:
        investor_tags = ["инвестиции", "портфель", "долгосрочное", "defi", "стратегия"]
        stmt = (
            self._base_query()
            .join(MaterialTag, MaterialTag.material_id == Material.id)
            .where(MaterialTag.tag.in_(investor_tags))
            .order_by(Material.priority.desc(), Material.updated_at.desc())
            .limit(limit)
        )
        return await self._execute_materials(stmt)

    async def get_material_stats(self) -> dict:
        total = await self.session.execute(select(func.count(Material.id)))
        total_count = total.scalar() or 0

        active = await self.session.execute(
            select(func.count(Material.id)).where(Material.status == MaterialStatus.READY)
        )
        active_count = active.scalar() or 0

        by_type = {}
        for material_type in MaterialType:
            result = await self.session.execute(
                select(func.count(Material.id)).where(Material.category == material_type.value)
            )
            by_type[material_type.value] = result.scalar() or 0

        return {
            "total": total_count,
            "active": active_count,
            "inactive": total_count - active_count,
            "by_type": by_type,
        }

    async def get_popular_tags(self, limit: int = 10) -> List[tuple[str, int]]:
        stmt = select(MaterialTag.tag, func.count(MaterialTag.tag)).group_by(MaterialTag.tag).order_by(
            func.count(MaterialTag.tag).desc()
        ).limit(limit)
        result = await self.session.execute(stmt)
        return result.all()

    async def update_material(self, material_id: str, **updates) -> Optional[Material]:
        material = await self.get_by_id(material_id)
        if not material:
            return None
        for key, value in updates.items():
            if hasattr(material, key):
                setattr(material, key, value)
        await self.session.flush()
        await self.session.refresh(material)
        return material

    async def delete_material(self, material_id: str) -> bool:
        material = await self.get_by_id(material_id)
        if not material:
            return False
        material.status = MaterialStatus.ARCHIVED
        await self.session.flush()
        return True

    # ------------------------------------------------------------------
    # Utilities for metrics
    # ------------------------------------------------------------------

    async def record_metric(
        self,
        material_id: str,
        metric_date,
        impressions: int = 0,
        clicks: int = 0,
        completions: int = 0,
        segment: Optional[str] = None,
        funnel_stage: Optional[str] = None,
    ) -> MaterialMetric:
        stmt = select(MaterialMetric).where(
            and_(MaterialMetric.material_id == material_id, MaterialMetric.metric_date == metric_date)
        )
        result = await self.session.execute(stmt)
        metric = result.scalar_one_or_none()
        if metric is None:
            metric = MaterialMetric(
                material_id=material_id,
                metric_date=metric_date,
                impressions=impressions,
                clicks=clicks,
                completions=completions,
                segment=segment,
                funnel_stage=funnel_stage,
            )
            self.session.add(metric)
        else:
            metric.impressions += impressions
            metric.clicks += clicks
            metric.completions += completions
            if segment:
                metric.segment = segment
            if funnel_stage:
                metric.funnel_stage = funnel_stage
        await self.session.flush()
        await self.session.refresh(metric)
        return metric
