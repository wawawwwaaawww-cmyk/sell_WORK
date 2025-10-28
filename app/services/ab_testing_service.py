"""A/B testing service with deterministic sampling and rich analytics."""

import asyncio
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, InputMediaDocument
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    ABTest,
    ABVariant,
    ABResult,
    ABAssignment,
    ABEvent,
    ABEventType,
    ABTestStatus,
    ABTestMetric,
    User,
)
from app.repositories.user_repository import UserRepository

VARIANT_CODES = ("A", "B", "C")
UNIQUE_EVENT_TYPES = {
    ABEventType.CLICKED,
    ABEventType.REPLIED,
    ABEventType.LEAD_CREATED,
    ABEventType.UNSUBSCRIBED,
    ABEventType.BLOCKED,
    ABEventType.DELIVERED,
}

DEFAULT_POPULATION_PERCENT = 20


@dataclass(slots=True)
class VariantDefinition:
    """Data holder for variant configuration before persistence."""
    title: str
    body: str
    media: List[Dict[str, Any]] = field(default_factory=list)
    buttons: List[Dict[str, Any]] = field(default_factory=list)
    parse_mode: str = "HTML"
    code: Optional[str] = None


class ABTestingService:
    """Service for managing A/B tests, deliveries, and analytics."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()

    async def create_test(
        self,
        name: str,
        created_by_admin_id: Optional[int] = None,
        variants: Sequence[VariantDefinition] = (),
        *,
        metric: ABTestMetric = ABTestMetric.CTR,
        sample_ratio: float = 0.1,
        observation_hours: int = 24,
        segment_filter: Optional[Dict[str, Any]] = None,
        send_at: Optional[datetime] = None,
        creator_user_id: Optional[int] = None,
        start_immediately: Optional[bool] = None,
    ) -> ABTest:
        """Create A/B test with provided variants."""
        if not variants or len(variants) != 2:
            raise ValueError("A/B test must contain exactly 2 variants")

        bounded_ratio = max(0.0, min(sample_ratio, 1.0))
        if bounded_ratio == 0:
            bounded_ratio = DEFAULT_POPULATION_PERCENT / 100

        creator_id = (
            created_by_admin_id
            if created_by_admin_id is not None
            else (creator_user_id if creator_user_id is not None else 0)
        )

        ab_test = ABTest(
            name=name,
            metric=metric,
            sample_ratio=bounded_ratio,
            observation_hours=observation_hours,
            segment_filter=segment_filter or {},
            created_by_admin_id=creator_id,
            send_at=send_at,
            status=ABTestStatus.DRAFT,
            variants_count=len(variants),
        )
        self.session.add(ab_test)
        await self.session.flush()

        for index, definition in enumerate(variants):
            code = definition.code or VARIANT_CODES[index]
            variant = ABVariant(
                ab_test_id=ab_test.id,
                variant_code=code,
                title=definition.title,
                body=definition.body,
                buttons=definition.buttons,
                media=definition.media,
                parse_mode=definition.parse_mode,
                weight=50,
                order_index=index,
            )
            self.session.add(variant)

        await self.session.flush()
        await self.session.refresh(ab_test)

        self.logger.info(
            "A/B test created",
            test_id=ab_test.id,
            creator=created_by_admin_id,
            variants=len(variants),
        )
        return ab_test

    async def start_pilot_phase(self, test_id: int, bot: Bot, throttle: float = 0.1) -> Dict[str, Any]:
        """Initiate the pilot sending phase for an A/B test."""
        test = await self.session.get(
            ABTest,
            test_id,
            options=[selectinload(ABTest.variants)],
        )
        if not test:
            raise ValueError(f"A/B test {test_id} not found")

        if test.status != ABTestStatus.DRAFT:
            return {"status": "already_started", "message": "Test is not in DRAFT state."}

        test.status = ABTestStatus.RUNNING
        test.started_at = self._now()
        await self.session.flush()

        user_repo = UserRepository(self.session)
        if hasattr(user_repo, "find_users_by_criteria"):
            audience = await user_repo.find_users_by_criteria(test.segment_filter)
        else:
            stmt = select(User).order_by(User.id)
            result = await self.session.execute(stmt)
            audience = list(result.scalars().all())
        
        pilot_size = int(len(audience) * test.sample_ratio)
        pilot_audience = audience[:pilot_size]

        variants = sorted(test.variants, key=lambda v: v.order_index or 0)
        variant_a, variant_b = variants[0], variants[1]

        assignments = []
        for i, user in enumerate(pilot_audience):
            variant = variant_a if i % 2 == 0 else variant_b
            assignment = ABAssignment(
                test_id=test.id,
                variant_id=variant.id,
                user_id=user.id,
                chat_id=user.telegram_id,
                delivery_status="PENDING",
            )
            assignments.append(assignment)
        
        self.session.add_all(assignments)
        await self.session.flush()

        delivery_summary = await self.deliver_assignments(assignments, bot, throttle=throttle)

        test.status = ABTestStatus.OBSERVE
        await self.session.flush()

        self.logger.info(
            "A/B test pilot phase completed",
            test_id=test_id,
            audience_size=len(audience),
            pilot_size=len(pilot_audience),
            sent=delivery_summary.get("sent"),
            failed=delivery_summary.get("failed"),
        )

        return {
            "status": "OBSERVE",
            "pilot_size": len(pilot_audience),
            "delivery": delivery_summary,
            "assignments": len(assignments),
        }

    async def start_test(
        self,
        test_id: int,
        *,
        bot: Bot,
        send_messages: bool = True,
        throttle: float = 0.1,
    ) -> Dict[str, Any]:
        """Backward-compatible entrypoint to launch pilot phase and deliver messages."""
        if not send_messages:
            summary = await self.start_pilot_phase(test_id, bot, throttle=0)
            return summary

        summary = await self.start_pilot_phase(test_id, bot, throttle=throttle)
        summary.setdefault("assignments", summary.get("pilot_size", 0))
        return summary

    async def deliver_assignments(self, assignments: List[ABAssignment], bot: Bot, throttle: float = 0.1) -> Dict[str, int]:
        """Deliver messages for a list of assignments."""
        sent = failed = 0
        for assignment in assignments:
            try:
                await self.session.refresh(assignment, ['variant', 'user'])
                variant = assignment.variant
                user = assignment.user

                if not variant or not user or user.is_blocked:
                    raise ValueError("Missing variant/user or user is blocked")

                await self._send_variant(bot, user.telegram_id, variant)

                assignment.first_delivery_at = self._now()
                assignment.delivery_status = "SENT"
                assignment.delivered_at = assignment.first_delivery_at
                assignment.message_id = None
                await self._ensure_event(assignment, ABEventType.DELIVERED, {"message_ids": []})
                sent += 1
            except Exception as exc:
                self.logger.error("Failed to deliver assignment", assignment_id=assignment.id, error=str(exc))
                assignment.delivery_status = "FAILED"
                assignment.delivery_error = str(exc)
                failed += 1
            
            await self.session.flush()
            await asyncio.sleep(throttle)
        
        return {"sent": sent, "failed": failed, "total": len(assignments)}

    async def select_winner(self, test_id: int) -> Optional[ABVariant]:
        """Analyze metrics and select a winning variant."""
        test = await self.session.get(ABTest, test_id)
        if not test or test.status != ABTestStatus.OBSERVE:
            return None

        now = self._now()
        observe_until = test.started_at + timedelta(hours=test.observation_hours)
        
        if now < observe_until:
            # Check for minimum data threshold extension
            delivered_count = await self.session.scalar(
                select(func.count(ABAssignment.id)).where(
                    ABAssignment.test_id == test_id,
                    ABAssignment.delivery_status == "SENT"
                )
            )
            if delivered_count < 200:
                test.observation_hours += 12
                await self.session.flush()
                self.logger.info("Extended observation period for test", test_id=test_id)
                return None

        analysis = await self.analyze_test_results(test_id)
        variants_payload = analysis.get("variants", [])
        
        if not variants_payload:
            return None

        if test.metric == ABTestMetric.CTR:
            winner_payload = max(variants_payload, key=lambda v: v.get("ctr", 0))
        elif test.metric == ABTestMetric.CR:
            winner_payload = max(variants_payload, key=lambda v: v.get("cr", 0))
        else: # Fallback to CTR
            winner_payload = max(variants_payload, key=lambda v: v.get("ctr", 0))

        # Tie-breaking rule
        if len(variants_payload) > 1:
            if test.metric == ABTestMetric.CTR and variants_payload[0]['ctr'] == variants_payload[1]['ctr']:
                winner_payload = min(variants_payload, key=lambda v: v.get("unsub_rate", 1))
            elif test.metric == ABTestMetric.CR and variants_payload[0]['cr'] == variants_payload[1]['cr']:
                 winner_payload = min(variants_payload, key=lambda v: v.get("unsub_rate", 1))


        winner_variant_id = winner_payload.get("variant_id")
        if winner_variant_id:
            test.winner_variant_id = winner_variant_id
            test.status = ABTestStatus.WINNER_PICKED
            await self.session.flush()
            return await self.session.get(ABVariant, winner_variant_id)
        
        return None

    async def start_winner_drip(self, test_id: int, bot: Bot):
        """Send the winning variant to the rest of the audience."""
        test = await self.session.get(ABTest, test_id, options=[selectinload(ABTest.variants)])
        if not test or test.status != ABTestStatus.WINNER_PICKED or not test.winner_variant_id:
            return {"status": "not_ready", "message": "Test is not in WINNER_PICKED state or winner not set."}

        winner_variant = await self.session.get(ABVariant, test.winner_variant_id)
        if not winner_variant:
            raise ValueError("Winner variant not found")

        # Get users from pilot phase
        pilot_user_ids_result = await self.session.execute(
            select(ABAssignment.user_id).where(ABAssignment.test_id == test_id)
        )
        pilot_user_ids = {row[0] for row in pilot_user_ids_result}

        user_repo = UserRepository(self.session)
        full_audience = await user_repo.find_users_by_criteria(test.segment_filter)
        
        remaining_audience = [user for user in full_audience if user.id not in pilot_user_ids]

        assignments = []
        for user in remaining_audience:
            assignment = ABAssignment(
                test_id=test.id,
                variant_id=winner_variant.id,
                user_id=user.id,
                chat_id=user.telegram_id,
                delivery_status="PENDING",
            )
            assignments.append(assignment)
        
        self.session.add_all(assignments)
        await self.session.flush()

        delivery_summary = await self.deliver_assignments(assignments, bot)

        test.status = ABTestStatus.COMPLETED
        test.finished_at = self._now()
        await self.session.flush()

        self.logger.info(
            "A/B test winner drip completed",
            test_id=test_id,
            remaining_audience_size=len(remaining_audience),
            sent=delivery_summary.get("sent"),
            failed=delivery_summary.get("failed"),
        )
        return {
            "status": "COMPLETED",
            "drip_size": len(remaining_audience),
            "delivery": delivery_summary,
        }

    async def analyze_test_results(self, test_id: int) -> Dict[str, Any]:
        """Calculate analytics for test without mutating snapshot."""
        stmt = text("""
            SELECT
                v.id as variant_id,
                v.variant_code,
                COUNT(a.id) as intended,
                SUM(CASE WHEN a.delivery_status = 'SENT' OR a.delivered_at IS NOT NULL THEN 1 ELSE 0 END) as delivered,
                (SELECT COUNT(DISTINCT e.user_id) FROM ab_events e WHERE e.variant_id = v.id AND e.event_type = 'clicked') as clicks,
                (SELECT COUNT(DISTINCT e.user_id) FROM ab_events e WHERE e.variant_id = v.id AND e.event_type = 'lead_created') as conversions,
                (SELECT COUNT(DISTINCT e.user_id) FROM ab_events e WHERE e.variant_id = v.id AND e.event_type = 'replied') as responses,
                (SELECT COUNT(DISTINCT e.user_id) FROM ab_events e WHERE e.variant_id = v.id AND e.event_type = 'unsubscribed') as unsubscribed
            FROM ab_variants v
            JOIN ab_assignments a ON a.variant_id = v.id
            WHERE v.ab_test_id = :test_id
            GROUP BY v.id, v.variant_code
        """)
        
        result = await self.session.execute(stmt, {"test_id": test_id})
        variants_payload = []
        for row in result:
            row_dict = row._asdict()
            delivered = row_dict.get('delivered', 0)
            clicks = row_dict.get('clicks', 0)
            conversions = row_dict.get('conversions', 0)
            intended = row_dict.get('intended', 0)
            responses = row_dict.get('responses', 0)
            unsubscribed = row_dict.get('unsubscribed', 0)

            ctr = (clicks / delivered) if delivered else 0.0
            cr = (conversions / delivered) if delivered else 0.0
            delivery_rate = (delivered / intended) if intended else 0.0
            response_rate = (responses / delivered) if delivered else 0.0
            unsubscribe_rate = (unsubscribed / delivered) if delivered else 0.0

            variants_payload.append({
                "variant_id": row_dict['variant_id'],
                "variant": row_dict['variant_code'],
                "intended": intended,
                "delivered": delivered,
                "clicks": clicks,
                "conversions": conversions,
                "responses": responses,
                "unsubscribed": unsubscribed,
                "unique_clicks": clicks,
                "leads": conversions,
                "ctr": ctr,
                "cr": cr,
                "delivery_rate": delivery_rate,
                "response_rate": response_rate,
                "unsub_rate": unsubscribe_rate,
            })

        test = await self.session.get(ABTest, test_id)
        winner_variant = None
        if variants_payload:
            winner_variant = max(
                variants_payload,
                key=lambda v: (v.get("leads", 0), v.get("ctr", 0), v.get("variant")),
            )

        return {
            "test_id": test.id,
            "name": test.name,
            "status": test.status.value if hasattr(test.status, "value") else str(test.status),
            "metric": test.metric.value if hasattr(test.metric, "value") else str(test.metric),
            "variants": variants_payload,
            "winner": winner_variant,
        }

    async def _ensure_event(
        self,
        assignment: ABAssignment,
        event_type: ABEventType,
        meta: Dict[str, Any],
    ) -> ABEvent:
        """Persist event once per assignment/event_type when uniqueness required."""
        if event_type in UNIQUE_EVENT_TYPES:
            existing_id = await self.session.scalar(
                select(ABEvent.id).where(
                    and_(
                        ABEvent.assignment_id == assignment.id,
                        ABEvent.event_type == event_type,
                    )
                )
            )
            if existing_id:
                return await self.session.get(ABEvent, existing_id)

        event = ABEvent(
            test_id=assignment.test_id,
            variant_id=assignment.variant_id,
            assignment_id=assignment.id,
            user_id=assignment.user_id,
            event_type=event_type,
            occurred_at=self._now(),
            meta=meta,
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def _send_variant(self, bot: Bot, chat_id: int, variant: ABVariant) -> List[int]:
        """Send variant content to user and return message ids."""
        keyboard = self._build_keyboard(variant.buttons, variant.id)
        message_ids = []

        media_items = [item for item in variant.media if item.get("type") in ("photo", "video", "document")]
        
        if len(media_items) > 1:
            # Send as media group
            media_group = []
            for i, item in enumerate(media_items):
                caption = variant.body if i == 0 else None
                if item['type'] == 'photo':
                    media_group.append(InputMediaPhoto(media=item['file_id'], caption=caption, parse_mode=variant.parse_mode))
                elif item['type'] == 'video':
                    media_group.append(InputMediaVideo(media=item['file_id'], caption=caption, parse_mode=variant.parse_mode))
                elif item['type'] == 'document':
                    media_group.append(InputMediaDocument(media=item['file_id'], caption=caption, parse_mode=variant.parse_mode))
            
            sent_messages = await bot.send_media_group(chat_id=chat_id, media=media_group)
            message_ids.extend([msg.message_id for msg in sent_messages])
            
            # Send text and keyboard separately if media group was sent without text
            if not any(item.get('caption') for item in media_group):
                msg = await bot.send_message(chat_id=chat_id, text=variant.body, reply_markup=keyboard, parse_mode=variant.parse_mode)
                message_ids.append(msg.message_id)

        elif len(media_items) == 1:
            item = media_items[0]
            if item['type'] == 'photo':
                msg = await bot.send_photo(chat_id=chat_id, photo=item['file_id'], caption=variant.body, reply_markup=keyboard, parse_mode=variant.parse_mode)
            elif item['type'] == 'video':
                msg = await bot.send_video(chat_id=chat_id, video=item['file_id'], caption=variant.body, reply_markup=keyboard, parse_mode=variant.parse_mode)
            elif item['type'] == 'document':
                msg = await bot.send_document(chat_id=chat_id, document=item['file_id'], caption=variant.body, reply_markup=keyboard, parse_mode=variant.parse_mode)
            message_ids.append(msg.message_id)
        else:
            # Just text
            msg = await bot.send_message(chat_id=chat_id, text=variant.body, reply_markup=keyboard, parse_mode=variant.parse_mode)
            message_ids.append(msg.message_id)

        return message_ids

    def _build_keyboard(self, buttons_data: Optional[List[Dict[str, Any]]], variant_id: int) -> Optional[InlineKeyboardMarkup]:
        """Create inline keyboard from stored button configuration."""
        if not buttons_data:
            return None

        builder = InlineKeyboardBuilder()
        for button in buttons_data:
            text = button.get("text")
            if not text:
                continue
            
            if "callback_data" in button:
                # Append test/variant info to callback data
                payload = f"{button['callback_data']}:test_id={variant_id}"
                builder.add(InlineKeyboardButton(text=text, callback_data=payload))
            elif "url" in button:
                # Append test/variant info to URL
                url = button["url"]
                separator = "&" if "?" in url else "?"
                url_with_tracking = f"{url}{separator}test_variant_id={variant_id}"
                builder.add(InlineKeyboardButton(text=text, url=url_with_tracking))

        builder.adjust(1)
        return builder.as_markup()

    def _now(self) -> datetime:
        """Return current UTC timestamp."""
        return datetime.now(timezone.utc)
