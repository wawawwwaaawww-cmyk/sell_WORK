"""Detection of information request intent (course details)."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Sequence

import structlog
from openai import AsyncOpenAI

from app.config import settings


class InquiryIntentService:
    """Classifies whether a message requests course/product details."""

    KEYWORD_PATTERNS: Sequence[re.Pattern[str]] = [
        re.compile(r"\bподробн(ее|ей|ость|ости)\b", re.IGNORECASE),
        re.compile(r"\bчто входит\b", re.IGNORECASE),
        re.compile(r"\bчто (за|там)\b.*курс", re.IGNORECASE),
        re.compile(r"\bрасскажите\b", re.IGNORECASE),
        re.compile(r"\bможно узнать\b", re.IGNORECASE),
        re.compile(r"\bинтересует\b.*курс", re.IGNORECASE),
        re.compile(r"\bиз чего состоит\b", re.IGNORECASE),
        re.compile(r"\bкак работает\b", re.IGNORECASE),
        re.compile(r"\bусловия\b", re.IGNORECASE),
    ]

    STRONG_PHRASES: Sequence[str] = [
        "расскажи подробнее",
        "дайте детали",
        "что входит в курс",
        "что будет на программе",
        "хочу узнать подробнее",
        "интересует программа",
        "что за курс",
        "что изучаем",
        "объясни как работает",
        "покажи программу",
        "распиши программу",
    ]

    SYSTEM_PROMPT = (
        "You classify if the user's message asks for details about products, courses, "
        "programs or services. Respond strictly with JSON: {\"info_intent\": true/false}."
    )

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)
        self._client: Optional[AsyncOpenAI] = None
        if settings.openai_api_key:
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    def _match_keywords(self, text: str) -> bool:
        text_lower = text.lower()
        if any(phrase in text_lower for phrase in self.STRONG_PHRASES):
            return True
        return any(pattern.search(text) for pattern in self.KEYWORD_PATTERNS)

    async def _llm_classify(self, text: str, context: Optional[str]) -> bool:
        if not self._client:
            return False

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Message: {text}\n"
                    f"Recent context: {context or '—'}\n"
                    "Reply with JSON {\"info_intent\": true/false}."
                ),
            },
        ]

        try:
            response = await self._client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=messages,
                max_tokens=50,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # pragma: no cover - network path
            self.logger.warning("inquiry_intent_llm_failed", error=str(exc))
            return False

        if not response.choices:
            return False

        content = response.choices[0].message.content or ""
        try:
            data: Dict[str, Any] = json.loads(content)
        except json.JSONDecodeError:
            self.logger.debug("inquiry_intent_json_parse_failed", raw=content)
            return False

        return bool(data.get("info_intent"))

    async def has_info_intent(self, text: str, *, context: Optional[str] = None) -> bool:
        if not text:
            return False
        if self._match_keywords(text):
            return True
        return await self._llm_classify(text, context)
