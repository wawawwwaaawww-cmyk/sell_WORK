"""Sales script generation and delivery service."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
from aiogram import Bot
from aiogram.types import InputFile, InlineKeyboardMarkup
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Lead, LeadEvent, LeadStatus, Message, MessageRole, User
from app.services.product_matching_service import ProductMatchingService
from app.services.survey_service import SurveyService


SCRIPT_HEADER_TEMPLATE = (
    "**Персональный скрипт v{version}**\n"
    "Обновлен: {timestamp}\n"
)


@dataclass
class SalesScriptResult:
    """Result of a sales script generation."""

    content: str
    version: int
    generated_at: datetime
    regenerated: bool
    inputs_hash: str
    model: str


class SalesScriptService:
    """Handles generation, storage, and delivery of sales scripts."""

    def __init__(
        self,
        session: AsyncSession,
        bot: Optional[Bot] = None,
        *,
        llm_client: Optional[AsyncOpenAI] = None,
    ):
        self.session = session
        self.bot = bot
        self._prompt_cache: Optional[str] = None
        self._logger = structlog.get_logger(__name__)
        self._llm_client = llm_client

    # --------------------------------------------------------------------- #
    # Public API
    # --------------------------------------------------------------------- #

    async def ensure_script(
        self,
        lead: Lead,
        user: User,
        *,
        force: bool = False,
        reason: str = "manual",
        actor_id: Optional[int] = None,
    ) -> SalesScriptResult:
        """Generate (or reuse) a sales script for a lead."""
        if not settings.sales_script_enabled:
            return self._reuse_existing(lead)

        fact_bundle = await self._build_fact_bundle(lead, user)
        inputs_hash = self._compute_inputs_hash(fact_bundle)
        existing_hash = lead.sales_script_inputs_hash or ""
        has_changed = inputs_hash != existing_hash
        regenerate = force or not lead.sales_script_md or has_changed

        if not regenerate:
            return self._reuse_existing(lead, inputs_hash=inputs_hash)

        script_text, model_used = await self._produce_script(fact_bundle)
        generated_at = datetime.now(timezone.utc)
        previous_version = lead.sales_script_version or 0
        is_regeneration = lead.sales_script_md is not None
        new_version = previous_version + 1 if is_regeneration else max(previous_version, 1)

        lead.sales_script_md = script_text
        lead.sales_script_version = new_version
        lead.sales_script_inputs_hash = inputs_hash
        lead.sales_script_generated_at = generated_at
        lead.sales_script_model = model_used

        await self.session.flush()

        event_type = "sales_script_regenerated" if is_regeneration else "sales_script_generated"
        await self._log_event(
            lead.id,
            event_type,
            {
                "version": new_version,
                "reason": reason,
                "actor_id": actor_id,
                "hash_changed": has_changed,
                "model": model_used,
            },
        )

        return SalesScriptResult(
            content=script_text,
            version=new_version,
            generated_at=generated_at,
            regenerated=is_regeneration,
            inputs_hash=inputs_hash,
            model=model_used,
        )

    async def refresh_for_user(
        self,
        user: User,
        *,
        reason: str,
        bot: Optional[Bot] = None,
    ) -> List[SalesScriptResult]:
        """Refresh scripts for all active leads of a user."""
        if not settings.sales_script_enabled:
            return []

        bot = bot or self.bot

        result: List[SalesScriptResult] = []
        stmt = (
            select(Lead)
            .where(Lead.user_id == user.id)
        )
        leads = (await self.session.execute(stmt)).scalars().all()
        for lead in leads:
            status_value: Optional[LeadStatus]
            if isinstance(lead.status, LeadStatus):
                status_value = lead.status
            else:
                try:
                    status_value = LeadStatus(lead.status)
                except Exception:
                    status_value = None
            if status_value in {LeadStatus.DONE, LeadStatus.PAID, LeadStatus.CANCELED}:
                continue
            if lead.sales_script_md is None:
                continue
            script_result = await self.ensure_script(
                lead,
                user,
                force=False,
                reason=reason,
            )
            result.append(script_result)
            if (
                bot
                and script_result.regenerated
                and settings.sales_script_regen_on_lead_update
            ):
                await self._post_update_if_possible(
                    lead,
                    script_result,
                    bot=bot,
                    reason=reason,
                )
        return result

    async def post_script_to_thread(
        self,
        lead: Lead,
        result: SalesScriptResult,
        *,
        chat_id: int,
        reply_to_message_id: int,
        manager_id: Optional[int] = None,
        auto_update: bool = False,
    ) -> Optional[int]:
        """Post script to channel thread."""
        if not self.bot:
            raise RuntimeError("Bot instance is required to post script to thread.")

        if not settings.sales_script_thread_post_on_click:
            return None

        header = SCRIPT_HEADER_TEMPLATE.format(
            version=result.version,
            timestamp=self._format_timestamp(result.generated_at),
        )
        text = f"{header}\n{result.content}".strip()

        keyboard = self._build_thread_keyboard(lead.id)
        try:
            message = await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="Markdown",
                reply_to_message_id=reply_to_message_id,
                disable_web_page_preview=True,
                reply_markup=keyboard,
            )
        except Exception as exc:  # pragma: no cover
            self._logger.warning(
                "sales_script_thread_post_failed",
                lead_id=lead.id,
                error=str(exc),
            )
            await self._log_event(
                lead.id,
                "sales_script_thread_post_failed",
                {
                    "chat_id": chat_id,
                    "reply_message_id": reply_to_message_id,
                    "auto_update": auto_update,
                    "reason": reason,
                    "error": str(exc),
                },
            )
            return None

        await self._log_event(
            lead.id,
            "sales_script_posted",
            {
                "message_id": message.message_id,
                "chat_id": chat_id,
                "auto_update": auto_update,
                "version": result.version,
            },
        )

        return message.message_id

    async def send_script_to_manager(
        self,
        lead: Lead,
        user: User,
        result: SalesScriptResult,
        *,
        manager_telegram_id: int,
        include_preview: bool = True,
    ) -> None:
        """Deliver script to a manager via direct message."""
        if not self.bot:
            raise RuntimeError("Bot instance is required to send script to manager.")

        header = SCRIPT_HEADER_TEMPLATE.format(
            version=result.version,
            timestamp=self._format_timestamp(result.generated_at),
        )
        text = f"{header}\n{result.content}".strip()

        if not settings.sales_script_split_long_messages or len(text) <= 3800:
            if include_preview:
                preview = (
                    f"Вы взяли заявку #{lead.id} от {user.first_name or user.username or user.telegram_id}.\n"
                    f"Ниже текущая версия скрипта."
                )
                await self.bot.send_message(manager_telegram_id, preview)
            await self.bot.send_message(
                manager_telegram_id,
                text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        else:
            if include_preview:
                await self.bot.send_message(
                    manager_telegram_id,
                    (
                        f"Вы взяли заявку #{lead.id}. Скрипт длинный, отправляю файлом."
                    ),
                )
            buffer = BytesIO(text.encode("utf-8"))
            buffer.name = f"lead_{lead.id}_script_v{result.version}.md"
            await self.bot.send_document(
                manager_telegram_id,
                document=InputFile(buffer),
                caption=f"Персональный скрипт v{result.version}",
            )

        await self._log_event(
            lead.id,
            "sales_script_sent_to_manager",
            {
                "manager_telegram_id": manager_telegram_id,
                "version": result.version,
            },
        )

    async def log_lead_card_posted(
        self,
        lead_id: int,
        *,
        chat_id: int,
        message_id: int,
    ) -> None:
        """Register lead card message information."""
        await self._log_event(
            lead_id,
            "lead_card_posted",
            {"chat_id": chat_id, "message_id": message_id},
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _reuse_existing(
        self,
        lead: Lead,
        *,
        inputs_hash: Optional[str] = None,
    ) -> SalesScriptResult:
        """Return existing script data as result."""
        generated_at = lead.sales_script_generated_at or datetime.now(timezone.utc)
        version = lead.sales_script_version or 1
        return SalesScriptResult(
            content=lead.sales_script_md or self._fallback_script({}),
            version=version,
            generated_at=generated_at,
            regenerated=False,
            inputs_hash=inputs_hash or lead.sales_script_inputs_hash or "",
            model=lead.sales_script_model or "unknown",
        )

    async def _build_fact_bundle(self, lead: Lead, user: User) -> Dict[str, Any]:
        survey_service = SurveyService(self.session)
        survey_answers = await survey_service.repository.get_user_answers(user.id)

        survey_pairs: List[Dict[str, str]] = []
        for answer in survey_answers:
            question = survey_service.questions.get(answer.question_code)
            if not question:
                continue
            option = question["options"].get(answer.answer_code)
            if not option:
                continue
            survey_pairs.append(
                {
                    "q": question["text"].replace("*", "").strip(),
                    "a": option["text"].strip(),
                }
            )

        product_data: Dict[str, Any] = {
            "name": "не указано",
            "score": None,
            "reason": "не указано",
        }
        try:
            matcher = ProductMatchingService(self.session)
            match_result = await matcher.match_for_user(
                user,
                trigger="sales_script",
                log_result=False,
                limit=1,
            )
            best = match_result.best_product
            if best:
                product_data["name"] = best.name
                product_data["score"] = round(match_result.score or 0.0, 4)
                product_data["reason"] = match_result.explanation or "не указано"
            else:
                product_data["score"] = round(match_result.score or 0.0, 4)
                product_data["reason"] = match_result.explanation or "не указано"
        except Exception as exc:  # pragma: no cover - defensive logging
            self._logger.warning(
                "sales_script_product_match_failed",
                lead_id=lead.id,
                user_id=user.id,
                error=str(exc),
            )

        recent_messages_stmt = (
            select(Message)
            .where(
                Message.user_id == user.id,
                Message.role == MessageRole.USER,
            )
            .order_by(Message.created_at.desc())
            .limit(20)
        )
        recent_messages = list((await self.session.execute(recent_messages_stmt)).scalars())
        recent_payload = [
            {
                "id": message.id,
                "role": "user",
                "text": message.text,
                "timestamp": message.created_at.isoformat() if message.created_at else None,
            }
            for message in reversed(recent_messages)
        ]

        segment_value = getattr(user.segment, "value", user.segment) if user.segment else "не указан"
        language = getattr(user, "language", None) or getattr(user, "lang", None) or "ru"

        lead_level = user.lead_level_percent if user.lead_level_percent is not None else "не указано"

        signature = {
            "interests": "не указано",
            "objections": "не указано",
            "questions": "не указано",
            "summary": "не указано",
        }

        bundle = {
            "lead": {
                "id": lead.id,
                "status": getattr(lead.status, "value", lead.status),
                "priority": lead.priority,
            },
            "user": {
                "name": f"{user.first_name or ''} {user.last_name or ''}".strip() or "не указано",
                "username": f"@{user.username}" if user.username else "не указано",
                "phone": user.phone or "не указано",
                "email": user.email or "не указано",
                "lang": language,
            },
            "segment": segment_value,
            "lead_level_percent": lead_level,
            "survey": survey_pairs,
            "recommended_product": product_data,
            "signature": signature,
            "recent_msgs": recent_payload,
        }
        return bundle

    def _compute_inputs_hash(self, bundle: Dict[str, Any]) -> str:
        payload = json.dumps(bundle, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _produce_script(self, bundle: Dict[str, Any]) -> tuple[str, str]:
        prompt_template = self._load_prompt()
        prompt = prompt_template.replace(
            "{LEAD_JSON}",
            json.dumps(bundle, ensure_ascii=False, indent=2),
        )

        if not settings.openai_api_key:
            return self._fallback_script(bundle), "fallback"

        client = self._llm_client or AsyncOpenAI(api_key=settings.openai_api_key)
        try:
            response = await client.chat.completions.create(
                model=settings.sales_script_model,
                temperature=settings.sales_script_temperature,
                max_tokens=settings.sales_script_max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
            )
            content = ""
            if response.choices:
                content = response.choices[0].message.content or ""
            content = content.strip()
            if not content:
                raise ValueError("Empty response from LLM")
            return content, settings.sales_script_model
        except Exception as exc:  # pragma: no cover - fallback path
            self._logger.error(
                "sales_script_generation_failed",
                error=str(exc),
            )
            return self._fallback_script(bundle), "fallback"

    def _load_prompt(self) -> str:
        if self._prompt_cache:
            return self._prompt_cache
        prompt_path = Path(settings.sales_script_prompt_path)
        try:
            self._prompt_cache = prompt_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._logger.error(
                "sales_script_prompt_missing",
                path=str(prompt_path),
            )
            self._prompt_cache = ""
        return self._prompt_cache

    def _fallback_script(self, bundle: Dict[str, Any]) -> str:
        """Provide a deterministic fallback script when LLM is unavailable."""
        user_info = bundle.get("user", {})
        name = user_info.get("name") or "клиент"
        product = bundle.get("recommended_product", {})
        product_name = product.get("name") or "подходящий продукт"

        return (
            f"1) Приветствие и ледокол\n"
            f"Здравствуйте, {name}! Давайте обсудим ваши цели и подберем комфортный сценарий.\n\n"
            f"2) SPIN\n"
            f"- S: Расскажите, с каким опытом вы подходите к инвестициям?\n"
            f"- P: Какие сложности сейчас мешают добраться до цели?\n"
            f"- I: Что изменится, если оставить всё, как есть?\n"
            f"- N: Что станет для вас самым ценным результатом работы?\n\n"
            f"3) Презентация\n"
            f"- A: Мы подготовили {product_name}, чтобы сосредоточиться на ваших задачах.\n"
            f"- I: Программа поможет структурировать шаги и получить точные рекомендации.\n"
            f"- D: Кейсы временно не указаны.\n"
            f"- A: Готовы записать вас на консультацию или отправить материалы?\n\n"
            f"4) Работа с возражениями\n"
            f"- «Дорого» → Давайте подберём формат или рассрочку, чтобы было комфортно.\n"
            f"- «Нет времени» → Согласуем удобный график и точки контроля прогресса.\n"
            f"- «Не получится» → Буду на связи и помогу пройти каждый шаг.\n\n"
            f"5) Следующие шаги\n"
            f"- Вариант 1: Закрепим слот консультации.\n"
            f"- Вариант 2: Отправлю чек-лист и расчёт внедрения.\n\n"
            f"6) Подсказки менеджеру\n"
            f"- Если молчит: уточните, что было самым полезным в диалоге.\n"
            f"- Если просит дешевле: предложите рассрочку или короткий стартовый модуль.\n"
            f"- Если спрашивает про риски: проговорите план действий и поддержку команды."
        )

    async def _log_event(self, lead_id: int, event_type: str, payload: Dict[str, Any]) -> None:
        event = LeadEvent(
            lead_id=lead_id,
            event_type=event_type,
            payload=payload or {},
        )
        self.session.add(event)
        await self.session.flush()

    async def _get_last_event(self, lead_id: int, event_type: str) -> Optional[LeadEvent]:
        stmt = (
            select(LeadEvent)
            .where(
                LeadEvent.lead_id == lead_id,
                LeadEvent.event_type == event_type,
            )
            .order_by(LeadEvent.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def _post_update_if_possible(
        self,
        lead: Lead,
        result: SalesScriptResult,
        *,
        bot: Bot,
        reason: str,
    ) -> None:
        script_posted_event = await self._get_last_event(lead.id, "sales_script_posted")
        if not script_posted_event:
            return
        card_event = await self._get_last_event(lead.id, "lead_card_posted")
        if not card_event:
            return
        chat_id = card_event.payload.get("chat_id")
        message_id = card_event.payload.get("message_id")
        if not chat_id or not message_id:
            return

        await self.post_script_to_thread(
            lead,
            result,
            chat_id=chat_id,
            reply_to_message_id=message_id,
            manager_id=None,
            auto_update=True,
        )

    def _build_thread_keyboard(self, lead_id: int) -> Optional[InlineKeyboardMarkup]:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        from app.utils.callbacks import Callbacks

        builder = InlineKeyboardBuilder()
        builder.button(
            text="🔄 Обновить скрипт",
            callback_data=f"{Callbacks.LEAD_SCRIPT_REFRESH}:{lead_id}",
        )
        builder.button(
            text="📥 Скопировать",
            callback_data=f"{Callbacks.LEAD_SCRIPT_COPY}:{lead_id}",
        )
        builder.adjust(1)
        return builder.as_markup()

    @staticmethod
    def _format_timestamp(dt: datetime) -> str:
        local_dt = dt.astimezone()
        return local_dt.strftime("%d.%m.%Y %H:%M %Z")
