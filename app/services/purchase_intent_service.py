"""Detection of purchase or consultation intent in user messages."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Sequence

import structlog
from openai import AsyncOpenAI

from app.config import settings


class PurchaseIntentService:
    """Service that classifies user messages for purchase intent."""

    KEYWORD_PATTERNS: Sequence[re.Pattern[str]] = [
        re.compile(r"\bкуп(лю|ить|и|им|ите|ишь)\b", re.IGNORECASE),
        re.compile(r"\bоплат(а|ить|ите|им|ишь)\b", re.IGNORECASE),
        re.compile(r"\bзапис(ать|аться|ал?и|ывай|ывайтесь|ываюсь)\b", re.IGNORECASE),
        re.compile(r"\bзаяв(к[ау]|и?ть)\b", re.IGNORECASE),
        re.compile(r"\bброн(ь|ирую|ировать)\b", re.IGNORECASE),
        re.compile(r"\bпредоплат", re.IGNORECASE),
        re.compile(r"\bкредит\b", re.IGNORECASE),
        re.compile(r"\bрассроч", re.IGNORECASE),
        re.compile(r"\bучаств(овать|ую|уй)\b", re.IGNORECASE),
        re.compile(r"\bпрод(ай|ать|ам|адите)\b", re.IGNORECASE),
        re.compile(r"\bоформ(лю|ить|ите|им|ление)\b", re.IGNORECASE),
        re.compile(r"\bвнес(ти|у) предоплат", re.IGNORECASE),
    ]

    STRONG_PHRASES: Sequence[str] = [
        "хочу купить",
        "давай купим",
        "готов оплатить",
        "хочу оплатить",
        "хочу оформить",
        "оформи мне",
        "запиши меня",
        "запишите меня",
        "записаться на курс",
        "возьму курс",
        "продай мне",
        "продайте курс",
        "хочу в рассрочку",
        "хочу рассрочку",
        "хочу в кредит",
        "оплачу",
        "готов заплатить",
        "предоплата",
        "бронь места",
        "участвовать хочу",
    ]

    SYSTEM_PROMPT = (
        "You are an intent classifier for a crypto trading education business. "
        "Decide if the user's message expresses a clear desire to purchase, book, "
        "pay, sign up, deposit, request credit/installments, or otherwise engage "
        "in a sales action (course purchase, consultation booking, participation). "
        "Answer strictly in JSON with a single boolean field 'purchase_intent'."
    )

    def __init__(self) -> None:
        self.logger = structlog.get_logger(__name__)
        self._client: Optional[AsyncOpenAI] = None
        if settings.openai_api_key:
            self._client = AsyncOpenAI(api_key=settings.openai_api_key)

    def _match_keywords(self, text: str) -> bool:
        """Check quick keyword heuristics."""
        text_lower = text.lower()
        if any(phrase in text_lower for phrase in self.STRONG_PHRASES):
            return True
        return any(pattern.search(text) for pattern in self.KEYWORD_PATTERNS)

    async def _llm_classify(self, text: str, context: Optional[str]) -> bool:
        """Use OpenAI to classify purchase intent."""
        if not self._client:
            return False

        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Message: {text}\n"
                    f"Recent context: {context or '—'}\n"
                    "Respond with JSON like {\"purchase_intent\": true or false}."
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
            self.logger.warning("purchase_intent_llm_failed", error=str(exc))
            return False

        if not response.choices:
            return False

        content = response.choices[0].message.content or ""
        try:
            data: Dict[str, Any] = json.loads(content)
        except json.JSONDecodeError:
            self.logger.debug("purchase_intent_json_parse_failed", raw=content)
            return False

        return bool(data.get("purchase_intent"))

    async def has_purchase_intent(self, text: str, *, context: Optional[str] = None) -> bool:
        """Return True when the message implies purchase intent."""
        if not text:
            return False

        if self._match_keywords(text):
            return True

        return await self._llm_classify(text, context)
