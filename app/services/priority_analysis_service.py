"""Services for classifying user responses in decline-survey flow."""

from __future__ import annotations

import json
from enum import Enum
from typing import Optional

import structlog
from openai import AsyncOpenAI

from app.config import settings


class PriorityIntent(str, Enum):
    """Intent labels for strategic priority choices."""

    RELIABILITY = "reliability"
    GROWTH = "growth"
    UNKNOWN = "unknown"


class ConfirmationIntent(str, Enum):
    """Intent labels for confirmation replies."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    UNKNOWN = "unknown"


class PriorityAnalysisService:
    """Classify user replies with GPT-4o mini and heuristic fallback."""

    def __init__(self) -> None:
        self._logger = structlog.get_logger(__name__)
        self._client: Optional[AsyncOpenAI] = None
        if settings.openai_api_key:
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)
            self._logger.info("priority_analysis_client_ready", model="gpt-4o-mini")
        else:
            self._logger.warning("priority_analysis_no_api_key")

    async def classify_priority(self, text: str) -> PriorityIntent:
        """Return intent describing whether user prefers reliability or growth."""

        normalized = (text or "").strip()
        self._logger.info(
            "priority_classification_started",
            text=normalized,
        )

        if not normalized:
            self._logger.info("priority_classification_empty")
            return PriorityIntent.UNKNOWN

        if self._client is None:
            result = self._fallback_priority(normalized)
            self._logger.info(
                "priority_classification_fallback",
                result=result.value,
            )
            return result

        try:
            response = await self._client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Ты помощник отдела продаж. Классифицируй ответ пользователя,"
                            " что для него важнее: надёжность капитала или возможность роста."
                            " Верни JSON с полем category = reliability | growth | unknown."
                            " Не добавляй никаких комментариев."
                        ),
                    },
                    {
                        "role": "user",
                        "content": normalized,
                    },
                ],
                max_completion_tokens=120,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content if response.choices else ""
            payload = json.loads(content) if content else {}
            category = str(payload.get("category", "")).strip().lower()
            mapped = {
                "reliability": PriorityIntent.RELIABILITY,
                "growth": PriorityIntent.GROWTH,
            }.get(category, PriorityIntent.UNKNOWN)
            self._logger.info(
                "priority_classification_completed",
                text=normalized,
                model_category=category or "",
                result=mapped.value,
            )
            if mapped is not PriorityIntent.UNKNOWN:
                return mapped
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.error(
                "priority_classification_error",
                error=str(exc),
                text=normalized,
                exc_info=True,
            )

        fallback = self._fallback_priority(normalized)
        self._logger.info(
            "priority_classification_fallback_used",
            result=fallback.value,
        )
        return fallback

    async def classify_confirmation(self, text: str) -> ConfirmationIntent:
        """Return whether reply conveys agreement or refusal."""

        normalized = (text or "").strip()
        self._logger.info(
            "confirmation_classification_started",
            text=normalized,
        )

        if not normalized:
            self._logger.info("confirmation_classification_empty")
            return ConfirmationIntent.UNKNOWN

        if self._client is None:
            result = self._fallback_confirmation(normalized)
            self._logger.info(
                "confirmation_classification_fallback",
                result=result.value,
            )
            return result

        try:
            response = await self._client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Определи отношение пользователя к предложению."
                            " Верни JSON с полем sentiment = positive | negative | unknown"
                            " в зависимости от того, согласен ли пользователь продолжить."
                            " Не добавляй пояснений."
                        ),
                    },
                    {"role": "user", "content": normalized},
                ],
                max_completion_tokens=120,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content if response.choices else ""
            payload = json.loads(content) if content else {}
            sentiment = str(payload.get("sentiment", "")).strip().lower()
            mapped = {
                "positive": ConfirmationIntent.POSITIVE,
                "negative": ConfirmationIntent.NEGATIVE,
            }.get(sentiment, ConfirmationIntent.UNKNOWN)
            self._logger.info(
                "confirmation_classification_completed",
                text=normalized,
                model_sentiment=sentiment or "",
                result=mapped.value,
            )
            if mapped is not ConfirmationIntent.UNKNOWN:
                return mapped
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.error(
                "confirmation_classification_error",
                error=str(exc),
                text=normalized,
                exc_info=True,
            )

        fallback = self._fallback_confirmation(normalized)
        self._logger.info(
            "confirmation_classification_fallback_used",
            result=fallback.value,
        )
        return fallback

    def _fallback_priority(self, text: str) -> PriorityIntent:
        """Heuristic priority classification for offline mode."""

        self._logger.debug("priority_fallback_evaluating", text=text)
        lowered = text.lower()
        if any(keyword in lowered for keyword in ["надёж", "надёжн", "стабил", "сохран", "безопас"]):
            return PriorityIntent.RELIABILITY
        if any(keyword in lowered for keyword in ["рост", "увелич", "разв", "прибы", "x", "кратн"]):
            return PriorityIntent.GROWTH
        return PriorityIntent.UNKNOWN

    def _fallback_confirmation(self, text: str) -> ConfirmationIntent:
        """Heuristic confirmation classification for offline mode."""

        self._logger.debug("confirmation_fallback_evaluating", text=text)
        lowered = text.lower()
        positive_markers = [
            "да",
            "ага",
            "конечно",
            "хорошо",
            "ок",
            "ладно",
            "жду",
            "давай",
            "готов",
            "интересно",
        ]
        negative_markers = [
            "нет",
            "неа",
            "не надо",
            "отказ",
            "не хочу",
            "позже",
            "не сейчас",
        ]
        if any(marker in lowered for marker in positive_markers):
            return ConfirmationIntent.POSITIVE
        if any(marker in lowered for marker in negative_markers):
            return ConfirmationIntent.NEGATIVE
        return ConfirmationIntent.UNKNOWN


__all__ = [
    "PriorityAnalysisService",
    "PriorityIntent",
    "ConfirmationIntent",
]
