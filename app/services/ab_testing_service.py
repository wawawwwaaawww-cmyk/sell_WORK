"""A/B testing service with deterministic sampling and rich analytics."""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
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

TEST_SAMPLE_RATIO = 0.30
DEFAULT_POPULATION_PERCENT = int(TEST_SAMPLE_RATIO * 100)
VARIANT_CODES = ("A", "B", "C")
UNIQUE_EVENT_TYPES = {
    ABEventType.CLICKED,
    ABEventType.REPLIED,
    ABEventType.LEAD_CREATED,
    ABEventType.PAYMENT_STARTED,
    ABEventType.PAYMENT_CONFIRMED,
    ABEventType.UNSUBSCRIBED,
    ABEventType.BLOCKED,
    ABEventType.DELIVERED,
}


@dataclass(slots=True)
class VariantDefinition:
    """Data holder for variant configuration before persistence."""

    title: str
    body: str
    content: Optional[List[Dict[str, Any]]] = None
    buttons: Optional[List[Dict[str, Any]]] = None
    code: Optional[str] = None


class ABTestingService:
    """Service for managing A/B tests, deliveries, and analytics."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()

    # ------------------------------------------------------------------
    # Test lifecycle
    # ------------------------------------------------------------------

    async def create_test(
        self,
        name: str,
        creator_user_id: int,
        variants: Sequence[VariantDefinition],
        *,
        metric: ABTestMetric = ABTestMetric.CTR,
        population_percent: Optional[int] = None,
        start_immediately: bool = True,
    ) -> ABTest:
        """Create A/B test with provided variants and optionally start it."""
        if not variants or len(variants) not in (2, 3):
            raise ValueError("A/B test must contain 2 or 3 variants")

        population_value = (
            max(1, min(100, population_percent))
            if population_percent is not None
            else DEFAULT_POPULATION_PERCENT
        )

        ab_test = ABTest(
            name=name,
            metric=metric,
            population=population_value,
            creator_user_id=creator_user_id,
            variants_count=len(variants),
            status=ABTestStatus.DRAFT,
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
                buttons={"buttons": definition.buttons} if definition.buttons else None,
                content=definition.content,
                weight=int(100 / len(variants)),
                order_index=index,
            )
            self.session.add(variant)

        await self.session.flush()
        await self.session.refresh(ab_test)

        if start_immediately:
            await self.start_test(ab_test.id)

        self.logger.info(
            "AB test created",
            test_id=ab_test.id,
            creator=creator_user_id,
            variants=len(variants),
        )

        return ab_test

    async def start_test(
        self,
        test_id: int,
        *,
        bot: Optional[Bot] = None,
        send_messages: bool = True,
        throttle: float = 0.05,
    ) -> Dict[str, Any]:
        """Start test by assigning audience and optionally delivering messages."""
        test = await self.session.get(
            ABTest,
            test_id,
            options=[selectinload(ABTest.variants)],
        )
        if not test:
            raise ValueError(f"A/B test {test_id} not found")

        if test.status_enum == ABTestStatus.COMPLETED:
            return {"status": "already_completed"}

        active_users = await self._get_active_users()
        assignments_created = await self._ensure_assignments(test, active_users)

        test.audience_size = len(active_users)
        test.test_size = assignments_created
        test.started_at = test.started_at or self._now()
        test.status = ABTestStatus.RUNNING

        await self.session.flush()

        delivery_summary: Dict[str, Any] = {"sent": 0, "failed": 0, "total": 0}
        if send_messages and bot is not None:
            delivery_summary = await self.deliver_pending_assignments(
                test_id,
                bot=bot,
                throttle=throttle,
            )

        self.logger.info(
            "AB test started",
            test_id=test_id,
            assignments=assignments_created,
            sent=delivery_summary.get("sent"),
            failed=delivery_summary.get("failed"),
        )

        return {
            "status": test.status.value if isinstance(test.status, str) else test.status.value,
            "assignments": assignments_created,
            "delivery": delivery_summary,
        }

    async def get_running_tests(self) -> List[ABTest]:
        """Return list of tests that are currently running."""
        stmt = select(ABTest).where(ABTest.status.in_([ABTestStatus.RUNNING, ABTestStatus.DRAFT]))
        return list((await self.session.execute(stmt)).scalars().all())

    async def deliver_pending_assignments(
        self,
        test_id: int,
        *,
        bot: Bot,
        throttle: float = 0.05,
        limit: Optional[int] = None,
    ) -> Dict[str, int]:
        """Deliver pending assignments for test."""
        stmt = (
            select(ABAssignment)
            .options(selectinload(ABAssignment.variant), selectinload(ABAssignment.user))
            .where(
                ABAssignment.test_id == test_id,
                ABAssignment.sent_at.is_(None),
            )
            .order_by(ABAssignment.id.asc())
        )
        if limit:
            stmt = stmt.limit(limit)

        assignments = list((await self.session.execute(stmt)).scalars().all())
        sent = failed = 0

        for assignment in assignments:
            variant = assignment.variant
            user = assignment.user
            if not variant or not user:
                continue

            try:
                message_id = await self._send_variant(bot, assignment, variant, user)
                timestamp = self._now()
                assignment.sent_at = timestamp
                assignment.delivered_at = timestamp
                assignment.message_id = message_id
                assignment.chat_id = user.telegram_id
                await self._ensure_event(assignment, ABEventType.DELIVERED, {})
                sent += 1
            except TelegramForbiddenError as forbidden_exc:
                await self._mark_delivery_failure(
                    assignment,
                    ABEventType.BLOCKED,
                    str(forbidden_exc),
                )
                failed += 1
            except TelegramBadRequest as bad_request:
                await self._mark_delivery_failure(
                    assignment,
                    ABEventType.UNSUBSCRIBED,
                    str(bad_request),
                )
                failed += 1
            except Exception as exc:  # noqa: BLE001
                await self._mark_delivery_failure(
                    assignment,
                    ABEventType.UNSUBSCRIBED,
                    str(exc),
                )
                failed += 1

            if throttle:
                await asyncio.sleep(throttle)

        return {"sent": sent, "failed": failed, "total": len(assignments)}

    async def complete_test(self, test_id: int) -> Tuple[bool, str, Dict[str, Any]]:
        """Mark test as completed and capture latest metrics snapshot."""
        test = await self.session.get(ABTest, test_id)
        if not test:
            return False, "A/B test not found", {}

        if test.status_enum == ABTestStatus.COMPLETED:
            analysis = await self.analyze_test_results(test_id)
            return True, "Already completed", analysis

        analysis = await self.snapshot_results(test_id)
        test.status = ABTestStatus.COMPLETED
        test.finished_at = self._now()
        await self.session.flush()

        return True, "Test completed successfully", analysis

    async def should_complete_test(
        self,
        test_id: int,
        *,
        min_hours: int = 24,
        min_sample_size: int = 30,
    ) -> bool:
        """Check if test is eligible for completion."""
        test = await self.session.get(ABTest, test_id)
        if not test or test.status_enum == ABTestStatus.COMPLETED:
            return False

        if not test.started_at:
            return False

        if self._now() - test.started_at < timedelta(hours=min_hours):
            return False

        sample_size = await self.session.scalar(
            select(func.count(ABAssignment.id)).where(ABAssignment.test_id == test_id)
        )
        return (sample_size or 0) >= min_sample_size

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    async def record_user_event(
        self,
        test_id: int,
        user_id: int,
        event_type: ABEventType,
        meta: Optional[Dict[str, Any]] = None,
    ) -> Optional[ABEvent]:
        """Record event against assignment if user participates in test."""
        assignment = await self.get_assignment(test_id, user_id)
        if not assignment:
            return None

        return await self._ensure_event(assignment, event_type, meta or {})

    async def get_assignment(self, test_id: int, user_id: int) -> Optional[ABAssignment]:
        """Get assignment for specific user and test."""
        stmt = (
            select(ABAssignment)
            .options(selectinload(ABAssignment.variant))
            .where(
                ABAssignment.test_id == test_id,
                ABAssignment.user_id == user_id,
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_assignment_by_message(self, chat_id: int, message_id: int) -> Optional[ABAssignment]:
        """Find assignment by Telegram chat/message identifiers."""
        stmt = (
            select(ABAssignment)
            .where(
                ABAssignment.chat_id == chat_id,
                ABAssignment.message_id == message_id,
            )
            .limit(1)
        )
        try:
            return (await self.session.execute(stmt)).scalar_one_or_none()
        except SQLAlchemyError as exc:  # pragma: no cover - defensive
            self.logger.warning(
                "ab_testing.assignment_lookup_failed",
                chat_id=chat_id,
                message_id=message_id,
                error=str(exc),
            )
            raise

    async def record_event_for_latest_assignment(
        self,
        user_id: int,
        event_type: ABEventType,
        meta: Optional[Dict[str, Any]] = None,
        within_hours: int = 48,
    ) -> Optional[ABEvent]:
        """Record event for most recent assignment of user."""
        cutoff = self._now() - timedelta(hours=within_hours)
        stmt = (
            select(ABAssignment)
            .where(
                ABAssignment.user_id == user_id,
                ABAssignment.delivered_at.isnot(None),
                ABAssignment.delivered_at >= cutoff,
            )
            .order_by(ABAssignment.delivered_at.desc())
        )

        assignments = list((await self.session.execute(stmt)).scalars().all())
        for assignment in assignments:
            existing = await self.session.scalar(
                select(ABEvent.id).where(
                    ABEvent.assignment_id == assignment.id,
                    ABEvent.event_type == event_type,
                )
            )
            if existing:
                continue
            return await self._ensure_event(assignment, event_type, meta or {})
        return None

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    async def analyze_test_results(self, test_id: int) -> Dict[str, Any]:
        """Calculate analytics for test without mutating snapshot."""
        test = await self.session.get(ABTest, test_id)
        if not test:
            return {"error": "Test not found"}

        try:
            variants_db = (
                await self.session.execute(
                    select(ABVariant).where(ABVariant.ab_test_id == test_id)
                )
            ).scalars().all()

            assignments = list(
                (await self.session.execute(
                    select(ABAssignment).where(ABAssignment.test_id == test_id)
                )).scalars()
            )
            events = list(
                (await self.session.execute(
                    select(ABEvent).where(ABEvent.test_id == test_id)
                )).scalars()
            )
        except SQLAlchemyError as exc:  # pragma: no cover - defensive
            self.logger.warning(
                "ab_testing.analytics_unavailable",
                test_id=test_id,
                error=str(exc),
            )
            return {
                "error": "ab_tables_missing",
                "test_id": test.id,
                "name": test.name,
                "status": test.status_enum.value,
                "metric": test.metric.value if isinstance(test.metric, ABTestMetric) else str(test.metric),
                "variants": [],
                "winner": None,
            }

        event_map: Dict[int, Dict[ABEventType, set[int]]] = {}
        for event in events:
            variant_events = event_map.setdefault(event.variant_id, {})
            event_type = event.event_type if isinstance(event.event_type, ABEventType) else ABEventType(event.event_type)
            variant_events.setdefault(event_type, set()).add(event.assignment_id)

        assignment_by_variant: Dict[int, List[ABAssignment]] = {}
        for assignment in assignments:
            assignment_by_variant.setdefault(assignment.variant_id, []).append(assignment)

        variants_payload: List[Dict[str, Any]] = []
        for variant in sorted(variants_db, key=lambda v: v.order_index or 0):
            variant_assignments = assignment_by_variant.get(variant.id, [])
            delivered = sum(1 for item in variant_assignments if item.delivered_at is not None)
            variant_events = event_map.get(variant.id, {})

            clicks = len(variant_events.get(ABEventType.CLICKED, set()))
            replies = len(variant_events.get(ABEventType.REPLIED, set()))
            leads = len(variant_events.get(ABEventType.LEAD_CREATED, set()))
            payments_started = len(variant_events.get(ABEventType.PAYMENT_STARTED, set()))
            payments_confirmed = len(variant_events.get(ABEventType.PAYMENT_CONFIRMED, set()))
            unsubscribed = len(variant_events.get(ABEventType.UNSUBSCRIBED, set()))
            blocked = len(variant_events.get(ABEventType.BLOCKED, set()))

            ctr = round((clicks / delivered), 4) if delivered else 0.0
            cr = round((leads / clicks), 4) if clicks else 0.0
            response_rate = round((replies / delivered), 4) if delivered else 0.0
            unsub_rate = round((unsubscribed / delivered), 4) if delivered else 0.0

            variants_payload.append(
                {
                    "variant": variant.variant_code,
                    "variant_id": variant.id,
                    "title": variant.title,
                    "delivered": delivered,
                    "unique_clicks": clicks,
                    "responses": replies,
                    "leads": leads,
                    "payment_started": payments_started,
                    "payment_confirmed": payments_confirmed,
                    "unsubscribed": unsubscribed,
                    "blocked": blocked,
                    "ctr": ctr,
                    "cr": cr,
                    "response_rate": response_rate,
                    "unsub_rate": unsub_rate,
                }
            )

        winner = self._determine_winner(variants_payload)

        return {
            "test_id": test.id,
            "name": test.name,
            "status": test.status_enum.value,
            "metric": test.metric.value if isinstance(test.metric, ABTestMetric) else str(test.metric),
            "population_percent": test.population,
            "variants": variants_payload,
            "winner": winner,
            "audience_size": test.audience_size,
            "test_size": test.test_size,
            "started_at": test.started_at,
            "finished_at": test.finished_at,
            "creator_user_id": test.creator_user_id,
        }

    async def snapshot_results(self, test_id: int) -> Dict[str, Any]:
        """Persist analytics snapshot into ab_results table and return metrics."""
        analysis = await self.analyze_test_results(test_id)
        if "error" in analysis:
            return analysis

        snapshot_time = self._now()

        for variant_data in analysis["variants"]:
            variant_code = variant_data["variant"]
            result = await self.session.scalar(
                select(ABResult).where(
                    and_(
                        ABResult.ab_test_id == test_id,
                        ABResult.variant_code == variant_code,
                    )
                )
            )

            if not result:
                result = ABResult(
                    ab_test_id=test_id,
                    variant_code=variant_code,
                )
                self.session.add(result)

            result.variant_id = variant_data["variant_id"]
            result.delivered = variant_data["delivered"]
            result.clicks = variant_data["unique_clicks"]
            result.conversions = variant_data["leads"]
            result.responses = variant_data["responses"]
            result.unsub = variant_data["unsubscribed"]
            result.payment_started = variant_data["payment_started"]
            result.payment_confirmed = variant_data["payment_confirmed"]
            result.blocked = variant_data["blocked"]
            result.snapshot_at = snapshot_time

        await self.session.flush()
        return analysis

    async def get_winner_variant(self, test_id: int) -> Optional[ABVariant]:
        """Return variant entity that wins according to analytics."""
        analysis = await self.analyze_test_results(test_id)
        winner_data = analysis.get("winner")
        if not winner_data:
            return None

        stmt = select(ABVariant).where(
            and_(
                ABVariant.ab_test_id == test_id,
                ABVariant.variant_code == winner_data["variant"],
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------
    # Legacy compatibility helpers
    # ------------------------------------------------------------------

    async def create_ab_test(
        self,
        name: str,
        variant_a_title: str,
        variant_a_body: str,
        variant_b_title: str,
        variant_b_body: str,
        population: int = DEFAULT_POPULATION_PERCENT,
        metric: ABTestMetric = ABTestMetric.CTR,
        variant_a_buttons: Optional[Dict[str, Any]] = None,
        variant_b_buttons: Optional[Dict[str, Any]] = None,
    ) -> ABTest:
        """Compatibility wrapper: create two-variant test without auto start."""
        variants = [
            VariantDefinition(
                title=variant_a_title,
                body=variant_a_body,
                buttons=(variant_a_buttons or {}).get("buttons"),
            ),
            VariantDefinition(
                title=variant_b_title,
                body=variant_b_body,
                buttons=(variant_b_buttons or {}).get("buttons"),
            ),
        ]
        return await self.create_test(
            name=name,
            creator_user_id=0,
            variants=variants,
            metric=metric,
            population_percent=population,
            start_immediately=False,
        )

    async def start_ab_test(self, test_id: int) -> Tuple[bool, str]:
        """Compatibility wrapper returning simplified tuple."""
        await self.start_test(test_id, send_messages=False)
        return True, "Test started"

    async def record_delivery(
        self,
        ab_test_id: int,
        variant_code: str,
        user_id: int,
    ) -> None:
        """Compatibility no-op: deliveries tracked via assignments."""
        await self.record_user_event(ab_test_id, user_id, ABEventType.DELIVERED, {"legacy": True})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_assignments(
        self,
        test: ABTest,
        users: Sequence[User],
    ) -> int:
        """Create assignments for eligible users deterministically."""
        stmt = select(ABAssignment).where(ABAssignment.test_id == test.id)
        existing = {assignment.user_id: assignment for assignment in (await self.session.execute(stmt)).scalars()}
        created = 0

        result = await self.session.execute(
            select(ABVariant).where(ABVariant.ab_test_id == test.id)
        )
        variants = sorted(result.scalars().all(), key=lambda v: v.order_index or 0)
        if not variants:
            raise ValueError("A/B test must have variants before starting")

        fallback: Optional[tuple[User, float]] = None

        for user in users:
            if not user.telegram_id:
                continue

            hash_value = self._compute_hash(test.id, user.telegram_id)
            if hash_value >= TEST_SAMPLE_RATIO:
                if fallback is None:
                    fallback = (user, hash_value)
                continue

            if user.id in existing:
                created += 1
                continue

            variant = self._choose_variant(hash_value, variants)
            assignment = ABAssignment(
                test_id=test.id,
                variant_id=variant.id,
                user_id=user.id,
                hash_value=round(hash_value, 6),
                chat_id=user.telegram_id,
            )
            self.session.add(assignment)
            created += 1

        if created == 0 and fallback:
            fallback_user, hash_value = fallback
            if fallback_user.id not in existing:
                variant = self._choose_variant(hash_value, variants)
                assignment = ABAssignment(
                    test_id=test.id,
                    variant_id=variant.id,
                    user_id=fallback_user.id,
                    hash_value=round(hash_value, 6),
                    chat_id=fallback_user.telegram_id,
                )
                self.session.add(assignment)
                created = 1

        await self.session.flush()
        return created

    async def _get_active_users(self) -> List[User]:
        """Return active user base for sampling."""
        stmt = select(User).where(User.is_blocked.is_(False))
        return list((await self.session.execute(stmt)).scalars().all())

    async def _mark_delivery_failure(
        self,
        assignment: ABAssignment,
        event_type: ABEventType,
        error: str,
    ) -> None:
        """Mark assignment delivery failure and store event."""
        assignment.failed_at = self._now()
        assignment.delivery_error = error
        await self._ensure_event(assignment, event_type, {"error": error})

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

    async def _send_variant(
        self,
        bot: Bot,
        assignment: ABAssignment,
        variant: ABVariant,
        user: User,
    ) -> Optional[int]:
        """Send variant content to user and return primary message id."""
        keyboard = self._build_keyboard(variant.buttons)
        message_id: Optional[int] = None

        content_items = variant.content or []
        if content_items:
            for index, item in enumerate(content_items):
                item_type = item.get("type")
                kwargs = {
                    "chat_id": user.telegram_id,
                }
                parse_mode = item.get("parse_mode")
                if item_type == "text":
                    kwargs["text"] = item.get("text") or variant.body
                    if parse_mode:
                        kwargs["parse_mode"] = parse_mode
                    if index == 0 and keyboard:
                        kwargs["reply_markup"] = keyboard
                    message = await bot.send_message(**kwargs)
                    message_id = message.message_id if message_id is None else message_id
                elif item_type == "photo":
                    kwargs["photo"] = item.get("file_id")
                    if item.get("caption"):
                        kwargs["caption"] = item["caption"]
                    if parse_mode:
                        kwargs["parse_mode"] = parse_mode
                    if index == 0 and keyboard:
                        kwargs["reply_markup"] = keyboard
                    message = await bot.send_photo(**kwargs)
                    message_id = message.message_id if message_id is None else message_id
                elif item_type == "video":
                    kwargs["video"] = item.get("file_id")
                    if item.get("caption"):
                        kwargs["caption"] = item["caption"]
                    if parse_mode:
                        kwargs["parse_mode"] = parse_mode
                    if index == 0 and keyboard:
                        kwargs["reply_markup"] = keyboard
                    message = await bot.send_video(**kwargs)
                    message_id = message.message_id if message_id is None else message_id
                elif item_type == "document":
                    kwargs["document"] = item.get("file_id")
                    if item.get("caption"):
                        kwargs["caption"] = item["caption"]
                    if parse_mode:
                        kwargs["parse_mode"] = parse_mode
                    if index == 0 and keyboard:
                        kwargs["reply_markup"] = keyboard
                    message = await bot.send_document(**kwargs)
                    message_id = message.message_id if message_id is None else message_id
                elif item_type == "audio":
                    kwargs["audio"] = item.get("file_id")
                    if item.get("caption"):
                        kwargs["caption"] = item["caption"]
                    if parse_mode:
                        kwargs["parse_mode"] = parse_mode
                    if index == 0 and keyboard:
                        kwargs["reply_markup"] = keyboard
                    message = await bot.send_audio(**kwargs)
                    message_id = message.message_id if message_id is None else message_id
                elif item_type == "voice":
                    kwargs["voice"] = item.get("file_id")
                    if index == 0 and keyboard:
                        kwargs["reply_markup"] = keyboard
                    message = await bot.send_voice(**kwargs)
                    message_id = message.message_id if message_id is None else message_id
                else:
                    self.logger.warning(
                        "Unsupported A/B variant content",
                        item_type=item_type,
                        assignment_id=assignment.id,
                    )
        else:
            message = await bot.send_message(
                chat_id=user.telegram_id,
                text=variant.body,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            message_id = message.message_id

        return message_id

    def _build_keyboard(self, buttons_data: Optional[Dict[str, Any]]) -> Optional[InlineKeyboardMarkup]:
        """Create inline keyboard from stored button configuration."""
        if not buttons_data:
            return None

        raw_buttons = buttons_data.get("buttons")
        if not raw_buttons:
            return None

        builder = InlineKeyboardBuilder()
        for button in raw_buttons:
            text = button.get("text")
            if not text:
                continue
            if "callback_data" in button:
                builder.add(InlineKeyboardButton(text=text, callback_data=button["callback_data"]))
            elif "url" in button:
                builder.add(InlineKeyboardButton(text=text, url=button["url"]))

        layout = buttons_data.get("layout")
        if isinstance(layout, int) and layout > 0:
            builder.adjust(layout)
        else:
            builder.adjust(1)
        return builder.as_markup()

    def _compute_hash(self, test_id: int, telegram_id: int) -> float:
        """Return deterministic float in [0, 1) based on identifiers."""
        payload = f"{test_id}:{telegram_id}".encode("utf-8")
        digest = hashlib.sha256(payload).digest()
        hash_int = int.from_bytes(digest[:8], byteorder="big", signed=False)
        return hash_int / float(2**64)

    def _choose_variant(self, hash_value: float, variants: Sequence[ABVariant]) -> ABVariant:
        """Deterministically choose variant for provided hash bucket."""
        if not variants:
            raise ValueError("Variants list cannot be empty")

        normalized = min(max(hash_value / TEST_SAMPLE_RATIO, 0.0), 0.999999)
        index = int(normalized * len(variants))
        if index >= len(variants):
            index = len(variants) - 1
        return variants[index]

    def _determine_winner(self, variants_payload: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Select winner based on CTR, then CR, then unsubscribe rate."""
        if not variants_payload:
            return None

        sorted_variants = sorted(
            variants_payload,
            key=lambda item: (
                item["ctr"],
                item["cr"],
                -item["unsub_rate"],
            ),
            reverse=True,
        )
        winner = sorted_variants[0]
        if winner["delivered"] == 0:
            return None

        return {
            "variant": winner["variant"],
            "ctr": winner["ctr"],
            "cr": winner["cr"],
            "unsub_rate": winner["unsub_rate"],
            "metric": "ctr",
            "score": winner["ctr"],
        }

    def _now(self) -> datetime:
        """Return current UTC timestamp."""
        return datetime.now(timezone.utc)
