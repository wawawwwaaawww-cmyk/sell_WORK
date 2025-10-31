"""LLM-driven sales dialog orchestrator tailored to the voronka specification."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import LeadStatus, User
from app.safety.validator import SafetyValidator
from app.services.lead_profile_service import LeadProfileService
from app.services.lead_service import LeadService
from app.services.llm_service import LLMService
from app.services.logging_service import ConversationLoggingService
from app.repositories.product_repository import ProductRepository
from app.utils.prompt_loader import prompt_loader


STAGE_PROMPT_KEYS: Dict[str, str] = {
    "opening": "stage_01_opening",
    "frame": "stage_02_frame",
    "goal": "stage_03_goal",
    "diagnostics": "stage_04_diagnostics",
    "gap": "stage_05_gap",
    "solution": "stage_06_solution",
    "proof": "stage_07_proof",
    "objections": "stage_08_objections",
    "scarcity": "stage_09_scarcity",
    "closing": "stage_10_closing",
}


OUTPUT_SCHEMA_PROMPT = """
Ты всегда отвечаешь строго в формате JSON (response_format=json_object) со структурой:
{
  "reply": "Основной ответ пользователю в 1-2 предложениях. Включай микро-саммари, когда закрываешь группу вопросов.",
  "lead_profile_updates": {
    "name": "Имя, если пользователь представился",
    "interest_level": "low|medium|high",
    "financial_goals": ["Сформированный список целей"],
    "investment_experience": "none|beginner|advanced",
    "objections": ["список формулировок возражений"],
    "emotional_type": "calm|logic|excited|anxious",
    "consultation_readiness": "ready|not_now|needs_think",
    "tone_preference": "friendly|neutral|humorous|expert|skeptical|anxious|other",
    "communication_style": "короткие|детальные|юмор|деловой|эмоциональный|другое",
    "entry_context": "как человек попал к нам / что его зацепило",
    "vector": "деньги|покой|управление|другое",
    "goal_picture": {
      "goal": "краткая формулировка цели",
      "six_months_signs": "что будет через 6 месяцев",
      "relief": "что перестанет беспокоить"
    },
    "diagnostics": {
      "facts": "как сейчас принимает решения/что делает",
      "implications": "какие состояния/мысли возникают",
      "causes": "почему так получается"
    },
    "priority_scale": {
      "current_level": "число 0-10 либо null",
      "target_level": "число 0-10 либо null"
    },
    "notable_quotes": ["сильные прямые цитаты пользователя"],
    "personal_value_drivers": ["что даст курсу лично для клиента"]
  },
  "scenario": "engaged|cautious",
  "next_stage": "opening|frame|goal|diagnostics|gap|solution|proof|objections|scarcity|closing",
  "lead_summary": "Сжатое саммари разговора своими словами клиента",
  "readiness_score": 0-100,
  "client_label": "ярлык типа клиента",
  "handoff_trigger": "строка причины передачи или null",
  "handoff_ready": true/false,
  "agent_notes": "служебные заметки, если нужны",
  "escalate_to_manager": true/false,
  "applied_tone": "friendly|neutral|humorous|expert"
}
Не добавляй никаких комментариев вне JSON. Пропускай ключи, если данных нет.
"""


SCENARIO_PROMPTS: Dict[str, str] = {
    "engaged": (
        "Пользователь вовлечен и интересуется ростом. Ускоряй темп, давай чуть больше экспертных инсайтов, "
        "но сохраняй уважительный тон. Повышай глубину вопросов и опирайся на амбиции."
    ),
    "cautious": (
        "Пользователь осторожен, пришёл за бонусом. Будь особенно мягким, разряжай атмосферу, "
        "делись простой пользой и приглашай к маленьким шагам без давления."
    ),
}


@dataclass
class SalesDialogOutcome:
    """Result of processing a single dialog turn."""

    reply_text: str
    metadata: Dict[str, Any]
    created_lead_id: Optional[int] = None
    raw_payload: Optional[Dict[str, Any]] = None
    fallback_used: bool = False


class SalesDialogService:
    """Coordinates LLM conversation, profile updates, and lead escalation."""

    def __init__(self, session: AsyncSession, user: User):
        self.session = session
        self.user = user
        self.logger = structlog.get_logger(__name__)
        self.conversation_logger = ConversationLoggingService(session)
        self.lead_profile_service = LeadProfileService(session)
        self.lead_service = LeadService(session)
        self.llm_service = LLMService(session=session, user=user)
        self.safety_validator = SafetyValidator()

        self.system_prompt = prompt_loader.load_prompt("voronka_system") or self._default_system_prompt()

    async def generate_reply(self) -> SalesDialogOutcome:
        """Generate AI reply, update lead profile, and optionally escalate."""
        profile = await self.lead_profile_service.get_or_create(self.user)
        history = await self.conversation_logger.get_last_messages(self.user.id, limit=12)
        stage_prompt = self._load_stage_prompt(profile.current_stage)

        if not settings.openai_api_key:
            fallback = self._fallback_message(profile)
            return SalesDialogOutcome(
                reply_text=fallback,
                metadata=self._build_metadata(profile, None, None, fallback=True),
                fallback_used=True,
            )

        product_catalog_prompt = await self._build_product_catalog_prompt()
        messages = self._compose_messages(profile, stage_prompt, history, product_catalog_prompt)
        raw_response = await self._request_agent(messages)

        payload = self._parse_payload(raw_response)
        if not payload:
            fallback = self._fallback_message(profile)
            return SalesDialogOutcome(
                reply_text=fallback,
                metadata=self._build_metadata(profile, None, None, fallback=True),
                fallback_used=True,
            )

        sanitized_text, _ = self.safety_validator.validate_response(payload.get("reply", ""))
        reply_text = sanitized_text or self._fallback_message(profile)

        profile = await self.lead_profile_service.apply_agent_payload(
            user=self.user,
            profile=profile,
            payload=payload,
        )

        # Treat escalate flag as a strong handoff signal
        if payload.get("escalate_to_manager"):
            profile.handoff_ready = True
            if not profile.handoff_trigger:
                profile.handoff_trigger = "escalation_requested"
            await self.lead_profile_service.repository.save(profile)

        created_lead_id = await self._maybe_create_lead(profile, payload)

        metadata = self._build_metadata(profile, payload, created_lead_id, fallback=False)

        return SalesDialogOutcome(
            reply_text=reply_text,
            metadata=metadata,
            created_lead_id=created_lead_id,
            raw_payload=payload,
            fallback_used=False,
        )

    async def _request_agent(self, messages: List[Dict[str, Any]]) -> Optional[str]:
        """Call the LLM with structured expectations."""
        try:
            response = await self.llm_service.get_completion(
                messages,
                purpose="sales_dialog_agent",
                max_tokens=900,
                expect_json=True,
            )
            return response.strip() if response else None
        except Exception as exc:
            self.logger.error("sales_dialog_llm_failure", error=str(exc), user_id=self.user.id)
            return None

    def _parse_payload(self, raw_response: Optional[str]) -> Optional[Dict[str, Any]]:
        """Parse JSON payload returned by the agent."""
        if not raw_response:
            return None
        try:
            sanitized = self._sanitize_json(raw_response)
            if not sanitized:
                return None
            payload = json.loads(sanitized)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            self.logger.warning("sales_dialog_payload_parse_failed", preview=raw_response[:200])
        return None

    async def _maybe_create_lead(
        self,
        profile: Any,
        payload: Dict[str, Any],
    ) -> Optional[int]:
        """Create a lead if readiness threshold is met."""
        if not profile.handoff_ready and not payload.get("handoff_trigger"):
            return None

        existing_leads = await self.lead_service.repository.get_user_leads(self.user.id)
        active = [
            lead
            for lead in existing_leads
            if lead.status in {LeadStatus.NEW, LeadStatus.TAKEN, LeadStatus.ASSIGNED}
        ]
        if active:
            return None

        trigger = profile.handoff_trigger or payload.get("handoff_trigger") or "sales_dialog_ready"
        summary = profile.summary_text or payload.get("lead_summary")

        lead = await self.lead_service.create_lead_from_user(
            self.user,
            trigger_event=trigger,
            conversation_summary=summary,
        )

        # Reset readiness so we do not create multiple leads for the same intent
        profile.handoff_ready = False
        await self.lead_profile_service.repository.save(profile)

        return lead.id if lead else None

    def _compose_messages(
        self,
        profile: Any,
        stage_prompt: str,
        history: List[Dict[str, Any]],
        product_catalog_prompt: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Build chat messages for the LLM."""
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
        ]

        scenario_prompt = SCENARIO_PROMPTS.get((profile.scenario or "").lower())
        if scenario_prompt:
            messages.append({"role": "system", "content": scenario_prompt})

        if stage_prompt:
            messages.append({"role": "system", "content": stage_prompt})

        if product_catalog_prompt:
            messages.append({"role": "system", "content": product_catalog_prompt})

        memory_block = self._build_memory_block(profile)
        messages.append({"role": "system", "content": memory_block})
        messages.append({"role": "system", "content": OUTPUT_SCHEMA_PROMPT})

        for entry in history[-10:]:
            role = entry.get("role")
            text = entry.get("text")
            if not text:
                continue
            chat_role = "assistant" if role in {"bot", "assistant"} else "user"
            messages.append({"role": chat_role, "content": text})

        return messages

    async def _build_product_catalog_prompt(self) -> Optional[str]:
        """Render active product catalog to steer the LLM toward trading programs."""
        try:
            repo = ProductRepository(self.session)
            products = await repo.get_active_products(limit=10)
        except Exception as exc:
            self.logger.warning("sales_dialog_products_fetch_failed", error=str(exc))
            return None

        if not products:
            return (
                "Каталог продуктов пуст. Веди к консультации и скажи, что менеджер расскажет про программы обучения трейдингу."
            )

        lines = [
            "Активные продукты школы (предлагай подходящие варианты, это программы по трейдингу криптовалют):"
        ]
        for product in products:
            price_text = ""
            if getattr(product, "price", None) is not None:
                price_text = f"{product.price} {product.currency or 'RUB'}"
            value_props = getattr(product, "value_props", None) or []
            value_text = "; ".join(str(item) for item in value_props if item)
            short_desc = getattr(product, "short_desc", None) or ""

            segment = f" Цена: {price_text}." if price_text else ""
            highlights = f" Ключевые тезисы: {value_text}." if value_text else ""

            lines.append(
                f"- {product.name}: {short_desc or 'углублённое обучение криптотрейдингу.'}{segment}{highlights}"
            )

        lines.append("Всегда связывай решение клиента с конкретным продуктом и приглашай на консультацию или запись.")
        return "\n".join(lines)

    def _build_memory_block(self, profile: Any) -> str:
        """Render profile data into text prompt."""
        data = profile.profile_data or {}
        summary = profile.summary_text or "нет"
        notes = profile.last_agent_notes or "—"

        def _fmt(value: Any, default: str = "—") -> str:
            if value is None:
                return default
            if isinstance(value, list):
                return ", ".join(str(item) for item in value) if value else default
            return str(value)

        goal_picture = data.get("goal_picture", {}) or {}
        diagnostics = data.get("diagnostics", {}) or {}
        priority_scale = data.get("priority_scale", {}) or {}

        return (
            "Контекст сессии:\n"
            f"- Текущая ветка: {profile.scenario or 'не определена'}\n"
            f"- Этап воронки: {profile.current_stage}\n"
            f"- Готовность (0-100): {profile.readiness_score}\n"
            f"- Ярлык клиента: {profile.client_label or '—'}\n"
            f"- Предпочитаемый тон: {_fmt(data.get('tone_preference'))}\n"
            f"- Стиль общения: {_fmt(data.get('communication_style'))}\n"
            f"- Последняя сводка: {summary}\n"
            f"- Служебные заметки: {notes}\n"
            "\n"
            "Собранные данные:\n"
            f"• Имя: {_fmt(data.get('name'))}\n"
            f"• Интерес: {_fmt(data.get('interest_level'))}\n"
            f"• Цели: {_fmt(data.get('financial_goals'))}\n"
            f"• Опыт: {_fmt(data.get('investment_experience'))}\n"
            f"• Возражения: {_fmt(data.get('objections'))}\n"
            f"• Эмоциональный тип: {_fmt(data.get('emotional_type'))}\n"
            f"• Контекст входа: {_fmt(data.get('entry_context'))}\n"
            f"• Вектор: {_fmt(data.get('vector'))}\n"
            f"• Картина цели: {_fmt(goal_picture.get('goal'))}\n"
            f"• Признаки через 6 мес: {_fmt(goal_picture.get('six_months_signs'))}\n"
            f"• Что уйдет из беспокойств: {_fmt(goal_picture.get('relief'))}\n"
            f"• Факты: {_fmt(diagnostics.get('facts'))}\n"
            f"• Следствия: {_fmt(diagnostics.get('implications'))}\n"
            f"• Причины: {_fmt(diagnostics.get('causes'))}\n"
            f"• Шкала сейчас: {_fmt(priority_scale.get('current_level'))}\n"
            f"• Шкала цель: {_fmt(priority_scale.get('target_level'))}\n"
            f"• Характерные цитаты: {_fmt(data.get('notable_quotes'))}\n"
            f"• Личная ценность от курса: {_fmt(data.get('personal_value_drivers'))}\n"
        )

    def _build_metadata(
        self,
        profile: Any,
        payload: Optional[Dict[str, Any]],
        lead_id: Optional[int],
        *,
        fallback: bool,
    ) -> Dict[str, Any]:
        """Prepare metadata for logging."""
        metadata: Dict[str, Any] = {
            "source": "sales_dialog_agent",
            "scenario": profile.scenario,
            "stage": profile.current_stage,
            "readiness_score": profile.readiness_score,
            "client_label": profile.client_label,
            "lead_profile_id": getattr(profile, "id", None),
            "handoff_ready": profile.handoff_ready,
            "lead_created_id": lead_id,
            "fallback_used": fallback,
        }
        if payload:
            metadata["raw_agent_payload"] = payload
            if payload.get("applied_tone"):
                metadata["applied_tone"] = payload["applied_tone"]
        return metadata

    def _load_stage_prompt(self, stage: Optional[str]) -> str:
        """Return stage-specific prompt text."""
        key = STAGE_PROMPT_KEYS.get((stage or "opening").lower(), STAGE_PROMPT_KEYS["opening"])
        return prompt_loader.load_prompt(key) or ""

    @staticmethod
    def _sanitize_json(raw: str) -> Optional[str]:
        """Trim Markdown fences and keep JSON body."""
        if not raw:
            return None
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            parts = cleaned.split("```")
            cleaned = "".join(parts[1:-1]) if len(parts) > 2 else parts[1]
            cleaned = cleaned.strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and start < end:
            return cleaned[start : end + 1]
        return cleaned if cleaned.startswith("{") and cleaned.endswith("}") else None

    def _fallback_message(self, profile: Any) -> str:
        """Backup response when LLM is unavailable."""
        if (profile.scenario or "").lower() == "engaged":
            return "Это Андрей, менеджер команды Азата. Чтобы довести вас до результата, уточню: что сейчас важнее всего по трейдингу?"
        return "На связи Андрей из школы Азата. Я зафиксировал ваш ответ и буду рядом, когда решите продолжить разговор о трейдинге."

    @staticmethod
    def _default_system_prompt() -> str:
        """Fallback system prompt when file is missing."""
        return (
            "Ты — ассистент школы Азата Валеева. Общайся дружелюбно, на «вы», в 1-2 предложениях, "
            "используй эмоджи к месту и соблюдай этапы воронки продаж. Соблюдай микро-саммари и собирай данные клиента."
        )
