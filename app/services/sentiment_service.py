"""Background sentiment classification service with aggregation utilities."""

from __future__ import annotations

import asyncio
import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import structlog
import openai
from openai import AsyncOpenAI
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import User, UserMessageScore
from app.repositories.system_settings_repository import SystemSettingsRepository


class SentimentLabel(str, Enum):
    """Supported sentiment labels."""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"

    @property
    def score(self) -> int:
        """Return numeric score according to specification."""
        if self is SentimentLabel.POSITIVE:
            return 1
        if self is SentimentLabel.NEGATIVE:
            return -1
        return 0


@dataclass(slots=True)
class SentimentJob:
    """Job payload for the sentiment worker."""

    user_id: int
    message_id: int
    text: str
    hash_value: str
    queued_at: datetime
    attempts: int = 0
    source: Optional[str] = None


@dataclass(slots=True)
class SentimentResult:
    """Result of a sentiment classification."""

    label: SentimentLabel
    score: int
    confidence: float
    model: str
    raw: Optional[dict[str, Any]] = None


class SentimentService:
    """Service orchestrating asynchronous sentiment classification and aggregation."""

    AUTO_SETTING_KEY = "sentiment:auto_classification_enabled"
    DEFAULT_MODEL = "gpt-4o-mini"
    MAX_ATTEMPTS = 3

    def __init__(self) -> None:
        self._queue: asyncio.Queue[SentimentJob] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []
        self._client: Optional[AsyncOpenAI] = None
        self._auto_enabled: bool = True
        self._started = False
        self._lock = asyncio.Lock()
        self._logger = structlog.get_logger(__name__)

    async def start(self, worker_count: int | None = None) -> None:
        """Start background workers."""
        async with self._lock:
            if self._started:
                return

            await self._load_auto_enabled()

            worker_total = worker_count or 3
            if settings.openai_api_key:
                self._client = AsyncOpenAI(api_key=settings.openai_api_key)
            else:
                self._logger.warning("sentiment_worker_no_api_key", fallback="neutral")
                self._client = None

            for index in range(worker_total):
                task = asyncio.create_task(
                    self._worker_loop(index),
                    name=f"sentiment-worker-{index}",
                )
                self._workers.append(task)

            self._started = True
            self._logger.info(
                "sentiment_workers_started",
                workers=worker_total,
                auto_enabled=self._auto_enabled,
            )

    async def stop(self) -> None:
        """Stop background workers."""
        async with self._lock:
            if not self._started:
                return

            for task in self._workers:
                task.cancel()
            await asyncio.gather(*self._workers, return_exceptions=True)
            self._workers.clear()
            self._started = False
            self._logger.info("sentiment_workers_stopped")

    async def enqueue_message(
        self,
        *,
        user_id: int,
        message_id: int,
        text: str,
        source: Optional[str] = None,
    ) -> None:
        """Queue message for classification."""
        if not self._started:
            self._logger.debug("sentiment_enqueue_skipped_not_started", user_id=user_id)
            return

        normalized = (text or "").strip()
        payload = f"{user_id}:{message_id}:{normalized}"
        hash_value = hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()

        job = SentimentJob(
            user_id=user_id,
            message_id=message_id,
            text=normalized,
            hash_value=hash_value,
            queued_at=datetime.now(timezone.utc),
            source=source,
        )
        await self._queue.put(job)
        self._logger.debug(
            "sentiment_job_enqueued",
            user_id=user_id,
            message_id=message_id,
            has_text=bool(normalized),
            source=source,
        )

    async def set_auto_enabled(self, enabled: bool, *, session: Optional[AsyncSession] = None) -> None:
        """Persist and apply auto-classification flag."""
        self._auto_enabled = enabled
        should_close = session is None
        if session is None:
            session = AsyncSessionLocal()
        try:
            repo = SystemSettingsRepository(session)
            await repo.set_value(
                self.AUTO_SETTING_KEY,
                enabled,
                description="Toggle for automatic sentiment classification",
            )
            await session.commit()
            self._logger.info("sentiment_auto_toggle", enabled=enabled)
        finally:
            if should_close:
                await session.close()

    async def is_auto_enabled(self) -> bool:
        """Return cached auto-classification flag."""
        return self._auto_enabled

    async def reconcile(self, batch_size: int = 500) -> None:
        """Recompute aggregates and lead levels as safety net."""
        async with AsyncSessionLocal() as session:
            await self._reconcile_batch(session, batch_size=batch_size)
            await session.commit()

    async def _load_auto_enabled(self) -> None:
        """Load persisted flag, defaulting to True."""
        async with AsyncSessionLocal() as session:
            repo = SystemSettingsRepository(session)
            value = await repo.get_value(self.AUTO_SETTING_KEY, default=True)
            if value is None:
                await repo.set_value(
                    self.AUTO_SETTING_KEY,
                    True,
                    description="Toggle for automatic sentiment classification",
                )
                await session.commit()
                self._auto_enabled = True
            else:
                self._auto_enabled = bool(value)

    async def _worker_loop(self, worker_index: int) -> None:
        """Continuously process sentiment jobs."""
        try:
            while True:
                job = await self._queue.get()
                try:
                    await self._process_job(job, worker_index)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover - defensive
                    self._logger.error(
                        "sentiment_job_failed",
                        error=str(exc),
                        user_id=job.user_id,
                        message_id=job.message_id,
                        attempts=job.attempts,
                        exc_info=True,
                    )
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            self._logger.debug("sentiment_worker_cancelled", worker=worker_index)
            raise

    async def _process_job(self, job: SentimentJob, worker_index: int) -> None:
        """Process a single sentiment classification job."""
        async with AsyncSessionLocal() as session:
            existing = await session.execute(
                select(UserMessageScore.id).where(
                    (UserMessageScore.hash == job.hash_value)
                    | (
                        (UserMessageScore.user_id == job.user_id)
                        & (UserMessageScore.message_id == job.message_id)
                    )
                )
            )
            if existing.scalar() is not None:
                self._logger.debug(
                    "sentiment_duplicate_skipped",
                    user_id=job.user_id,
                    message_id=job.message_id,
                )
                return

            result = await self._classify(job)
            if (
                result.model.startswith("fallback:")
                and result.model not in {"fallback:no_api_key"}
                and job.attempts + 1 < self.MAX_ATTEMPTS
            ):
                backoff_seconds = min(30, 2 ** (job.attempts or 0))
                job.attempts += 1
                await asyncio.sleep(backoff_seconds)
                await self._queue.put(job)
                self._logger.warning(
                    "sentiment_requeued_after_fallback",
                    user_id=job.user_id,
                    message_id=job.message_id,
                    attempts=job.attempts,
                    model=result.model,
                    backoff=backoff_seconds,
                )
                await session.rollback()
                return
            await self._store_result(session, job, result)
            await session.commit()
            self._logger.info(
                "sentiment_classified",
                user_id=job.user_id,
                message_id=job.message_id,
                label=result.label.value,
                confidence=result.confidence,
                model=result.model,
                worker=worker_index,
            )

    async def _classify(self, job: SentimentJob) -> SentimentResult:
        """Classify message using quick rules or LLM."""
        if not job.text:
            return SentimentResult(
                label=SentimentLabel.NEUTRAL,
                score=SentimentLabel.NEUTRAL.score,
                confidence=0.0,
                model="rule:empty",
            )

        if not await self.is_auto_enabled():
            return SentimentResult(
                label=SentimentLabel.NEUTRAL,
                score=SentimentLabel.NEUTRAL.score,
                confidence=0.0,
                model="disabled",
            )

        if self._client is None:
            return SentimentResult(
                label=SentimentLabel.NEUTRAL,
                score=SentimentLabel.NEUTRAL.score,
                confidence=0.0,
                model="fallback:no_api_key",
            )

        try:
            response = await self._client.chat.completions.create(
                model=settings.llm_model or self.DEFAULT_MODEL,
                temperature=0,
                max_tokens=50,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты классификатор тональности. Классифицируй сообщение пользователя "
                            "как positive, neutral или negative. Возвращай JSON вида "
                            '{"label":"positive|neutral|negative","confidence":0.0-1.0}. '
                            "Если сообщение без текста, вложение или sticker — выбирай neutral "
                            "с confidence 0.0. Не добавляй никакого другого текста."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Сообщение пользователя:\n{job.text}",
                    },
                ],
            )
            payload = self._extract_json_response(response)
            label_value = str(payload.get("label", "neutral")).strip().lower()
            confidence_raw = payload.get("confidence", 0.0)
            try:
                label = SentimentLabel(label_value)
            except ValueError:
                label = SentimentLabel.NEUTRAL
            confidence = self._clamp_confidence(confidence_raw)
            return SentimentResult(
                label=label,
                score=label.score,
                confidence=confidence,
                model=response.model or settings.llm_model or self.DEFAULT_MODEL,
                raw=payload,
            )
        except (openai.APIError, openai.APIConnectionError, openai.RateLimitError) as api_exc:
            self._logger.warning(
                "sentiment_llm_api_error",
                error=str(api_exc),
                user_id=job.user_id,
            )
            return SentimentResult(
                label=SentimentLabel.NEUTRAL,
                score=SentimentLabel.NEUTRAL.score,
                confidence=0.0,
                model="fallback:api_error",
            )
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.error(
                "sentiment_llm_failure",
                error=str(exc),
                user_id=job.user_id,
                exc_info=True,
            )
            return SentimentResult(
                label=SentimentLabel.NEUTRAL,
                score=SentimentLabel.NEUTRAL.score,
                confidence=0.0,
                model="fallback:exception",
            )

    async def _store_result(
        self,
        session: AsyncSession,
        job: SentimentJob,
        result: SentimentResult,
    ) -> None:
        """Persist audit record and update user aggregates."""
        entry = UserMessageScore(
            user_id=job.user_id,
            message_id=job.message_id,
            label=result.label.value,
            score=result.score,
            model=result.model,
            confidence=result.confidence,
            hash=job.hash_value,
        )
        session.add(entry)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            self._logger.debug(
                "sentiment_integrity_skipped",
                user_id=job.user_id,
                message_id=job.message_id,
            )
            return

        user = await session.get(User, job.user_id)
        if user is None:
            self._logger.warning("sentiment_user_missing", user_id=job.user_id)
            return

        user.counter = (user.counter or 0) + result.score
        if result.label is SentimentLabel.POSITIVE:
            user.pos_count = (user.pos_count or 0) + 1
        elif result.label is SentimentLabel.NEGATIVE:
            user.neg_count = (user.neg_count or 0) + 1
        else:
            user.neu_count = (user.neu_count or 0) + 1
        user.scored_total = (user.scored_total or 0) + 1

        await self._update_lead_level(session, user)
        await session.flush()

    async def _update_lead_level(self, session: AsyncSession, user: User) -> None:
        """Recalculate lead level percent when enough data collected."""
        if (user.scored_total or 0) < 10:
            user.lead_level_percent = None
            user.lead_level_updated_at = None
            return

        scores_stmt = (
            select(UserMessageScore.score)
            .where(UserMessageScore.user_id == user.id)
            .order_by(UserMessageScore.evaluated_at.desc())
            .limit(10)
        )
        results = await session.execute(scores_stmt)
        last_ten = [row[0] for row in results.fetchall()]
        if len(last_ten) < 10:
            user.lead_level_percent = None
            user.lead_level_updated_at = None
            return

        avg = sum(last_ten) / 10
        percent = round(((avg + 1) / 2) * 100)
        percent = max(0, min(100, percent))
        user.lead_level_percent = percent
        user.lead_level_updated_at = datetime.now(timezone.utc)

    async def _reconcile_batch(self, session: AsyncSession, batch_size: int) -> None:
        """Reconcile aggregates with user_message_scores audit records."""
        aggregates_stmt = (
            select(
                UserMessageScore.user_id,
                func.count().label("total"),
                func.sum(
                    case((UserMessageScore.label == SentimentLabel.POSITIVE.value, 1), else_=0)
                ).label("pos"),
                func.sum(
                    case((UserMessageScore.label == SentimentLabel.NEUTRAL.value, 1), else_=0)
                ).label("neu"),
                func.sum(
                    case((UserMessageScore.label == SentimentLabel.NEGATIVE.value, 1), else_=0)
                ).label("neg"),
                func.sum(UserMessageScore.score).label("net"),
                func.max(UserMessageScore.evaluated_at).label("last_scored"),
            )
            .group_by(UserMessageScore.user_id)
            .order_by(func.max(UserMessageScore.evaluated_at).desc())
            .limit(batch_size)
        )

        aggregates = await session.execute(aggregates_stmt)
        for row in aggregates:
            user = await session.get(User, row.user_id)
            if user is None:
                continue
            user.pos_count = row.pos or 0
            user.neu_count = row.neu or 0
            user.neg_count = row.neg or 0
            user.scored_total = row.total or 0
            user.counter = row.net or 0
            await self._update_lead_level(session, user)

    def _extract_json_response(self, response: Any) -> dict[str, Any]:
        """Extract JSON payload from OpenAI response object."""
        if not response or not getattr(response, "choices", None):
            return {}
        message = response.choices[0].message
        content = getattr(message, "content", None)
        if isinstance(content, list):
            parts = []
            for item in content:
                text = item.get("text") if isinstance(item, dict) else None
                if text:
                    parts.append(text)
            content = "".join(parts)
        if not isinstance(content, str):
            return {}
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _clamp_confidence(value: Any) -> float:
        """Clamp confidence value to [0, 1]."""
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            confidence = 0.0
        return max(0.0, min(1.0, confidence))


sentiment_service = SentimentService()

__all__ = ["sentiment_service", "SentimentService", "SentimentLabel"]
