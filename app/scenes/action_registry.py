"""Action registry for scenario engine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

import structlog
from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.bonus_service import BonusService
from app.services.llm_service import LLMContext, LLMService
from app.services.materials_service import MaterialService
from app.utils.prompt_loader import prompt_loader


ActionHandler = Callable[["ActionContext", Mapping[str, Any]], Awaitable[Any]]

_PLACEHOLDER_MESSAGE = (
    "⚙️ Сценарий в разработке. Мы скоро добавим полноценный ответ."
)


@dataclass
class ActionOutcome:
    """Structured response from action execution."""

    message_text: Optional[str] = None
    buttons: List[Dict[str, str]] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionContext:
    """Context passed to action handlers."""

    session: AsyncSession
    user: User
    bot: Optional[Bot] = None
    chat_id: Optional[int] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    @property
    def logger(self):
        base = structlog.get_logger()
        return base.bind(user_id=self.user.id)


class ActionExecutionError(RuntimeError):
    """Raised when action execution fails."""


class ActionRegistry:
    """Registry storing mapping between action names and async callables."""

    def __init__(self):
        self._actions: Dict[str, ActionHandler] = {}
        self._logger = structlog.get_logger()

    def register(self, name: str, handler: ActionHandler) -> None:
        if not asyncio.iscoroutinefunction(handler):
            raise TypeError(f"Action '{name}' must be an async callable")
        self._actions[name] = handler
        self._logger.debug("action_registered", action=name)

    def unregister(self, name: str) -> None:
        self._actions.pop(name, None)

    def ensure_placeholder(self, name: str) -> None:
        if name not in self._actions:
            self.register(name, _make_placeholder(name))

    def get(self, name: str) -> ActionHandler:
        try:
            return self._actions[name]
        except KeyError as exc:
            raise KeyError(f"Action '{name}' is not registered") from exc

    async def execute(
        self,
        name: str,
        context: ActionContext,
        params: Optional[Mapping[str, Any]] = None,
    ) -> Optional[ActionOutcome]:
        handler = self.get(name)
        parameters = params or {}
        try:
            result = await handler(context, parameters)
        except Exception as exc:  # pylint: disable=broad-except
            context.logger.error(
                "action_failed",
                action=name,
                params=dict(parameters),
                error=str(exc),
            )
            raise ActionExecutionError(name) from exc

        if result is None:
            return None
        if isinstance(result, ActionOutcome):
            return result
        return ActionOutcome(data={"result": result})

    @classmethod
    def with_default_actions(cls) -> "ActionRegistry":
        registry = cls()
        registry.register("noop", _noop)
        registry.register("log_event", _log_event)
        registry.register("store_context", _store_context)
        registry.register("send_message", _send_message)
        registry.register("send_llm_response", _send_llm_response)
        registry.register("deliver_bonus", _deliver_bonus)
        registry.register("send_materials", _send_materials)
        registry.register("offer_options", _offer_options)
        registry.ensure_placeholder("ask_survey_question")
        registry.ensure_placeholder("calculate_segment")
        registry.ensure_placeholder("propose_slots")
        registry.ensure_placeholder("schedule_consultation")
        registry.ensure_placeholder("schedule_reminder")
        registry.ensure_placeholder("create_lead")
        registry.ensure_placeholder("post_manager_notification")
        registry.ensure_placeholder("collect_payment_preferences")
        registry.ensure_placeholder("generate_payment_link")
        registry.ensure_placeholder("schedule_followup")
        registry.ensure_placeholder("enqueue_ab_test")
        registry.ensure_placeholder("trigger_onboarding")
        registry.ensure_placeholder("evaluate_reengagement")
        return registry


async def _noop(context: ActionContext, _: Mapping[str, Any]) -> None:
    """Base action that does nothing."""
    context.logger.debug("action_noop")


async def _log_event(context: ActionContext, params: Mapping[str, Any]) -> Optional[ActionOutcome]:
    """Log event placeholder; integrates with EventService later."""
    event_type = params.get("event_type", "unknown")
    payload = params.get("payload", {})
    context.logger.info(
        "action_log_event",
        event_type=event_type,
        payload=payload,
    )
    return ActionOutcome(data={"logged_event": event_type})


async def _store_context(context: ActionContext, params: Mapping[str, Any]) -> Optional[ActionOutcome]:
    """Store arbitrary values into context extras."""
    for key, value in params.items():
        context.extras[key] = value
    context.logger.debug("action_store_context", stored=list(params.keys()))
    return ActionOutcome(data={"stored": dict(params)})


async def _send_message(context: ActionContext, params: Mapping[str, Any]) -> ActionOutcome:
    """Construct message response based on static template."""
    template = str(params.get("template", ""))
    raw_buttons = params.get("buttons") or []
    if not isinstance(raw_buttons, list):
        raise ActionExecutionError("send_message.buttons must be a list")
    buttons: List[Dict[str, str]] = []
    for button in raw_buttons:
        if not isinstance(button, Mapping):
            raise ActionExecutionError("send_message button must be mapping")
        text_value = str(button.get("text", ""))
        callback_value = button.get("callback_data") or button.get("callback")
        url_value = button.get("url")
        button_map: Dict[str, str] = {"text": text_value}
        if url_value:
            button_map["url"] = str(url_value)
        else:
            button_map["callback_data"] = str(callback_value or "")
        buttons.append(button_map)
    return ActionOutcome(message_text=template, buttons=buttons)


async def _send_llm_response(context: ActionContext, params: Mapping[str, Any]) -> ActionOutcome:
    prompt_file = params.get("prompt")
    scenario_prompt = None
    if prompt_file:
        prompt_name = Path(str(prompt_file)).stem
        scenario_prompt = prompt_loader.load_prompt(prompt_name)

    payload_context = params.get("context") if isinstance(params.get("context"), Mapping) else {}
    funnel_stage = payload_context.get("funnel_stage") or getattr(context.user.funnel_stage, "value", None) or "new"
    messages_history = context.extras.get("messages_history")
    if not isinstance(messages_history, list):
        messages_history = []

    llm_context = LLMContext(
        user=context.user,
        messages_history=messages_history,
        survey_summary=context.extras.get("survey_summary"),
        candidate_materials=context.extras.get("candidate_materials"),
        relevant_products=context.extras.get("relevant_products"),
        funnel_stage=str(funnel_stage),
        scenario_prompt=scenario_prompt,
    )

    llm_service = LLMService()
    llm_response = await llm_service.generate_response(llm_context)

    buttons: List[Dict[str, str]] = []
    for button in llm_response.buttons:
        if not isinstance(button, Mapping):
            continue
        text_value = str(button.get("text", ""))
        callback_value = button.get("callback_data") or button.get("callback")
        url_value = button.get("url")
        button_map: Dict[str, str] = {"text": text_value}
        if url_value:
            button_map["url"] = str(url_value)
        else:
            button_map["callback_data"] = str(callback_value or "")
        buttons.append(button_map)

    data = {
        "next_action": llm_response.next_action,
        "confidence": llm_response.confidence,
        "safety": [getattr(issue, "code", str(issue)) for issue in llm_response.safety_issues],
    }

    return ActionOutcome(
        message_text=llm_response.reply_text,
        buttons=buttons,
        data=data,
    )


async def _deliver_bonus(context: ActionContext, params: Mapping[str, Any]) -> ActionOutcome:
    bonus_service = BonusService(context.session)
    bonus_text = await bonus_service.get_welcome_bonus_text()
    raw_buttons = params.get("buttons") or []
    buttons: List[Dict[str, str]] = []
    if raw_buttons:
        for button in raw_buttons:
            if not isinstance(button, Mapping):
                continue
            text_value = str(button.get("text", ""))
            callback_value = button.get("callback_data") or button.get("callback")
            url_value = button.get("url")
            button_map: Dict[str, str] = {"text": text_value}
            if url_value:
                button_map["url"] = str(url_value)
            else:
                button_map["callback_data"] = str(callback_value or "")
            buttons.append(button_map)
    else:
        buttons = [
            {"text": "🎯 Пройти тест", "callback_data": "survey:start"},
            {"text": "📞 Консультация", "callback_data": "consult:schedule"},
        ]

    return ActionOutcome(
        message_text=bonus_text,
        buttons=buttons,
        data={"bonus_delivered": True},
    )


async def _send_materials(context: ActionContext, params: Mapping[str, Any]) -> ActionOutcome:
    material_service = MaterialService(context.session)
    segment = params.get("segment") or getattr(context.user.segment, "value", None) or "cold"
    funnel_stage = params.get("funnel_stage") or params.get("stage") or "engaged"
    limit = params.get("limit") or 3
    try:
        limit_value = max(1, int(limit))
    except (TypeError, ValueError):
        limit_value = 3

    materials = await material_service.get_materials_by_context(
        context=str(funnel_stage),
        segment=str(segment),
        limit=limit_value,
    )
    if not materials:
        segment_value = segment if isinstance(segment, str) else getattr(context.user.segment, "value", "cold")
        materials = await material_service.get_materials_for_segment(segment=segment_value, limit=limit_value)

    message_text = material_service.format_materials_for_delivery(materials)
    material_ids = [getattr(material, "id", None) for material in materials if getattr(material, "id", None) is not None]

    return ActionOutcome(
        message_text=message_text,
        data={"materials": material_ids},
    )


async def _offer_options(context: ActionContext, params: Mapping[str, Any]) -> ActionOutcome:
    template = str(params.get("template", "Выберите дальнейшее действие:"))
    raw_buttons = params.get("buttons") or []
    buttons: List[Dict[str, str]] = []
    for button in raw_buttons:
        if not isinstance(button, Mapping):
            continue
        text_value = str(button.get("text", ""))
        callback_value = button.get("callback_data") or button.get("callback")
        url_value = button.get("url")
        button_map: Dict[str, str] = {"text": text_value}
        if url_value:
            button_map["url"] = str(url_value)
        else:
            button_map["callback_data"] = str(callback_value or "")
        buttons.append(button_map)
    if not buttons:
        buttons = [{"text": "↩️ Назад", "callback_data": "noop:return"}]
    return ActionOutcome(message_text=template, buttons=buttons)


def _make_placeholder(name: str) -> ActionHandler:
    async def _placeholder(context: ActionContext, params: Mapping[str, Any]) -> None:
        context.logger.warning(
            "action_placeholder",
            action=name,
            params=dict(params),
        )
    return _placeholder


__all__ = [
    "ActionRegistry",
    "ActionContext",
    "ActionOutcome",
    "ActionExecutionError",
]
