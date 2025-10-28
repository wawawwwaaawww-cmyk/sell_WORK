
"""Material service for managing educational content."""

from __future__ import annotations

from typing import Dict, List, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Material, MaterialType, MaterialVersion, UserSegment
from app.repositories.material_repository import MaterialRepository


class MaterialService:
    """High level operations around marketing materials catalogue."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = MaterialRepository(session)
        self.logger = structlog.get_logger()

    # ------------------------------------------------------------------
    # Retrieval helpers
    # ------------------------------------------------------------------

    async def get_materials_for_segment(
        self,
        segment: UserSegment | str,
        funnel_stage: Optional[str] = None,
        limit: int = 5,
    ) -> List[Material]:
        """Return materials matched to a user segment and funnel stage."""
        try:
            segment_enum = self._segment_from_string(segment) if isinstance(segment, str) else segment
            if segment_enum is None:
                segment_enum = UserSegment.COLD
            materials = await self.repository.get_materials_by_segment(
                segment=segment_enum,
                limit=limit,
                stage=funnel_stage,
            )
            if not materials:
                materials = await self.repository.get_recent_materials(limit=limit)
            return materials
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error("materials_for_segment_error", error=str(exc))
            return []

    async def get_educational_materials(self, limit: int = 5) -> List[Material]:
        """Return general educational content used for onboarding."""
        try:
            materials = await self.repository.get_materials_by_type(MaterialType.ARTICLE, limit)
            if len(materials) < limit:
                supplementary = await self.repository.get_materials_for_newbies(limit - len(materials))
                materials.extend([m for m in supplementary if m not in materials])
            return materials[:limit]
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error("educational_materials_error", error=str(exc))
            return []

    async def get_case_studies(
        self,
        segment: Optional[str] = None,
        limit: int = 3,
    ) -> List[Material]:
        """Return case studies prioritised by segment."""
        try:
            seg_value = segment or UserSegment.WARM.value
            try:
                seg_enum = UserSegment(seg_value)
            except ValueError:
                seg_enum = UserSegment.WARM

            materials = await self.repository.get_materials_by_segment(seg_enum, limit * 2)
            case_materials = [m for m in materials if m.category == MaterialType.CASE.value]
            if len(case_materials) < limit:
                fallback = await self.repository.get_materials_by_type(MaterialType.CASE, limit)
                case_materials.extend([m for m in fallback if m not in case_materials])
            return case_materials[:limit]
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error("case_materials_error", error=str(exc))
            return []

    async def get_reviews_and_testimonials(self, limit: int = 3) -> List[Material]:
        """Return review/testimonial content."""
        try:
            reviews = await self.repository.get_materials_by_type(MaterialType.REVIEW, limit * 2)
            return reviews[:limit]
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error("reviews_materials_error", error=str(exc))
            return []

    async def get_materials_by_context(
        self,
        context: str,
        segment: str,
        limit: int = 3,
    ) -> List[Material]:
        """Return materials matched to message context and segment."""
        try:
            segment_enum = self._segment_from_string(segment)
            tags = await self._extract_tags_from_context(context)
            materials: List[Material] = []
            if tags:
                materials = await self.repository.get_materials_by_tags(tags, limit)
            if len(materials) < limit and segment_enum:
                more = await self.repository.get_materials_by_segment(segment_enum, limit * 2)
                for material in more:
                    if material not in materials:
                        materials.append(material)
                    if len(materials) >= limit:
                        break
            if len(materials) < limit:
                fallback = await self.repository.get_recent_materials(limit)
                for material in fallback:
                    if material not in materials:
                        materials.append(material)
                    if len(materials) >= limit:
                        break
            return materials[:limit]
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error("materials_by_context_error", error=str(exc))
            return []

    async def get_recommended_materials_for_user(
        self,
        user_segment: UserSegment,
        user_interests: List[str],
        funnel_stage: str = "engaged",
        limit: int = 5,
    ) -> List[Material]:
        """Return personalised recommendations for the user."""
        try:
            materials: List[Material] = []
            if funnel_stage in ["new", "welcomed"]:
                materials = await self.repository.get_materials_for_newbies(limit)
            elif funnel_stage in ["engaged"] and user_segment == UserSegment.WARM:
                materials = await self.repository.get_materials_for_traders(limit)
            elif funnel_stage in ["qualified"] and user_segment == UserSegment.HOT:
                materials = await self.repository.get_materials_for_investors(limit)

            if not materials:
                materials = await self.repository.get_materials_by_segment(user_segment, limit)

            if user_interests:
                interest_materials = await self.repository.get_materials_by_tags(user_interests, limit)
                for material in interest_materials:
                    if material not in materials:
                        materials.insert(0, material)

            deduped: List[Material] = []
            for material in materials:
                if material not in deduped:
                    deduped.append(material)
                if len(deduped) >= limit:
                    break
            return deduped
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error("recommended_materials_error", error=str(exc))
            return []

    async def get_material_performance_analytics(self) -> Dict[str, any]:
        """Return aggregated analytics for materials catalogue."""
        try:
            stats = await self.repository.get_material_stats()
            popular_tags = await self.repository.get_popular_tags()
            return {
                "total_materials": stats["total"],
                "active_materials": stats["active"],
                "materials_by_type": stats["by_type"],
                "popular_tags": popular_tags,
                "engagement_summary": {"note": "Material engagement tracking planned"},
            }
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error("material_analytics_error", error=str(exc))
            return {"error": "Failed to fetch analytics"}

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def format_materials_for_delivery(self, materials: List[Material]) -> str:
        """Render material list into Telegram-friendly markdown."""
        if not materials:
            return "📚 К сожалению, подходящие материалы сейчас недоступны. Обратитесь к менеджеру за помощью."

        lines: List[str] = ["📚 **Полезные материалы для тебя:**", ""]
        for index, material in enumerate(materials, start=1):
            lines.append(f"{index}. **{material.title}**")
            preview = self._material_preview(material)
            if preview:
                lines.append(f"   _{preview}_")
            link = self._material_link(material)
            if link:
                lines.append(f"   🔗 [Открыть материал]({link})")
            lines.append("")
        lines.append("💡 *Эти материалы подобраны специально под твой уровень и цели!*")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Tracking & utilities
    # ------------------------------------------------------------------

    async def track_material_engagement(
        self,
        user_id: int,
        material_id: str,
        engagement_type: str = "viewed",
    ) -> bool:
        """Log engagement event (placeholder until engagement table implemented)."""
        try:
            self.logger.info(
                "material_engagement_tracked",
                user_id=user_id,
                material_id=material_id,
                engagement_type=engagement_type,
            )
            return True
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.error("material_engagement_error", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _extract_tags_from_context(self, context: str) -> List[str]:
        """Derive recommended tags from conversation context."""
        keyword_map = {
            # Beginner topics
            "основы": ["основы", "новичкам", "базовые"],
            "начинающий": ["новичкам", "начинающим", "простые"],
            "безопасность": ["безопасность", "защита", "риски"],
            # Trading topics
            "торговля": ["трейдинг", "торговля", "сделки"],
            "анализ": ["анализ", "технический", "графики"],
            "стратегия": ["стратегии", "методы", "подходы"],
            # Investment topics
            "инвестиции": ["инвестиции", "портфель", "долгосрочное"],
            "defi": ["defi", "decentralized", "протоколы"],
            "nft": ["nft", "токены", "коллекции"],
            # General
            "bitcoin": ["bitcoin", "btc", "биткоин"],
            "ethereum": ["ethereum", "eth", "эфириум"],
            "альткоины": ["альткоины", "altcoins", "токены"],
        }

        context_lower = context.lower()
        extracted: List[str] = []
        for key_phrase, tags in keyword_map.items():
            if key_phrase in context_lower:
                extracted.extend(tags)
        return list({tag.lower() for tag in extracted})

    @staticmethod
    def _segment_from_string(segment: str) -> Optional[UserSegment]:
        if not segment:
            return None
        try:
            return UserSegment(segment.lower())
        except ValueError:
            return None

    @staticmethod
    def _material_preview(material: Material, max_length: int = 120) -> Optional[str]:
        version: Optional[MaterialVersion] = material.active_version
        source_text = version.extracted_text if version and version.extracted_text else material.summary
        if not source_text:
            return None
        preview = source_text.strip().replace("\n", " ")
        if len(preview) > max_length:
            preview = preview[: max_length - 3] + "..."
        return preview

    @staticmethod
    def _material_link(material: Material) -> Optional[str]:
        version: Optional[MaterialVersion] = material.active_version
        if version:
            return version.primary_asset_url
        return None
