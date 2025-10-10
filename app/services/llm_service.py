"""LLM service for OpenAI integration with policy layer."""

import json
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

import structlog
import openai
from openai import AsyncOpenAI

from app.config import settings
from app.models import User, UserSegment
from app.utils.prompt_loader import prompt_loader
from app.utils.callbacks import Callbacks
from app.safety.validator import SafetyValidator, SafetyIssue

LOW_CONFIDENCE_THRESHOLD = 0.35



@dataclass
class LLMContext:
    """Context data for LLM requests."""
    user: User
    messages_history: List[Dict[str, str]]
    survey_summary: Optional[str] = None
    candidate_materials: List[Dict[str, Any]] = None
    relevant_products: List[Dict[str, Any]] = None
    funnel_stage: str = "new"
    scenario_prompt: Optional[str] = None


@dataclass 
class LLMResponse:
    """Structured LLM response."""
    reply_text: str
    buttons: List[Dict[str, str]]
    next_action: str
    confidence: float
    safety_issues: List[SafetyIssue]
    is_safe: bool


class PolicyLayer:
    """Policy layer for deterministic business logic."""
    
    def __init__(self):
        self.logger = structlog.get_logger()
    
    def apply_segment_policy(self, context: LLMContext, response: Dict[str, Any]) -> Dict[str, Any]:
        """Apply segment-specific policies to the response."""
        user_segment = context.user.segment
        
        # Hot segment prioritizes offers
        if user_segment == UserSegment.HOT:
            if response.get("next_action") == "ask" and context.user.lead_score > 12:
                response["next_action"] = "offer_consult"
                
        # Cold segment needs more education
        elif user_segment == UserSegment.COLD:
            if response.get("next_action") == "offer_payment":
                response["next_action"] = "show_materials"
        
        # Warm segment balances education and sales
        elif user_segment == UserSegment.WARM:
            if response.get("next_action") == "offer_payment" and context.user.lead_score < 8:
                response["next_action"] = "offer_consult"
        
        return response
    
    def prevent_repetitive_offers(self, context: LLMContext, response: Dict[str, Any]) -> Dict[str, Any]:
        """Prevent repetitive offers without new value."""
        # Check last few messages for repeated actions
        recent_actions = []
        for msg in context.messages_history[-3:]:  # Last 3 messages
            if msg.get("role") == "bot" and "next_action" in msg.get("meta", {}):
                recent_actions.append(msg["meta"]["next_action"])
        
        current_action = response.get("next_action")
        
        # If same action repeated twice, add material or escalate
        if recent_actions.count(current_action) >= 2:
            if current_action in ["offer_consult", "offer_payment"]:
                response["next_action"] = "show_materials"
                response["reply_text"] += "\n\nДавай сначала посмотрим на несколько примеров..."
            else:
                response["next_action"] = "escalate_to_manager"
        
        return response
    
    def enforce_escalation_rules(self, context: LLMContext, response: Dict[str, Any]) -> Dict[str, Any]:
        """Enforce automatic escalation rules."""
        # Low confidence threshold
        if response.get("confidence", 0) < 0.3:
            response["next_action"] = "escalate_to_manager"
            
        # Complex technical questions
        technical_keywords = ["блокчейн", "майнинг", "хеш", "консенсус", "смарт-контракт"]
        if any(keyword in response["reply_text"].lower() for keyword in technical_keywords):
            if response.get("confidence", 0) < 0.7:
                response["next_action"] = "escalate_to_manager"
        
        return response


class LLMService:
    """Service for LLM interactions with safety and policy enforcement."""
    
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.policy_layer = PolicyLayer()
        self.safety_validator = SafetyValidator()
        self.logger = structlog.get_logger()
        
        # Load system prompts
        self.system_prompt = prompt_loader.get_system_prompt()
        self.safety_policies = prompt_loader.get_safety_policies()
    
    async def generate_response(self, context: LLMContext) -> LLMResponse:
        """Generate LLM response with safety and policy validation."""
        try:
            if not settings.openai_api_key:
                self.logger.warning("OpenAI API key is not configured; using fallback response")
                return self._fallback_response()
            messages = self._build_messages(context)
            raw_content = await self._call_chat_completion(messages)
            payload = self._try_parse_json(raw_content)

            if payload is None:
                self.logger.error(
                    "LLM response could not be parsed as JSON",
                    preview=raw_content[:200] if raw_content else None,
                )
                return self._fallback_response()

            llm_response = self.policy_layer.apply_segment_policy(context, payload)
            llm_response = self.policy_layer.prevent_repetitive_offers(context, llm_response)
            llm_response = self.policy_layer.enforce_escalation_rules(context, llm_response)

            sanitized_text, safety_issues = self.safety_validator.validate_response(
                llm_response.get("reply_text", "")
            )

            is_safe = self.safety_validator.is_safe_for_auto_send(safety_issues)
            escalate_required = self.safety_validator.should_escalate_to_manager(
                llm_response.get("confidence", 0), safety_issues
            )

            low_confidence = llm_response.get("confidence", 0) < LOW_CONFIDENCE_THRESHOLD
            empty_reply = not sanitized_text.strip()
            unsafe_payload = not is_safe or any(issue.severity == "high" for issue in safety_issues)

            if escalate_required or low_confidence or empty_reply or unsafe_payload:
                self.logger.info(
                    "Policy fallback triggered",
                    escalate=escalate_required,
                    low_confidence=low_confidence,
                    empty_reply=empty_reply,
                    unsafe=unsafe_payload,
                )
                return self._escalation_response(safety_issues)

            return LLMResponse(
                reply_text=sanitized_text,
                buttons=llm_response.get("buttons", []),
                next_action=llm_response.get("next_action", "ask"),
                confidence=llm_response.get("confidence", 0.5),
                safety_issues=safety_issues,
                is_safe=is_safe
            )

        except Exception as e:
            self.logger.error("LLM service error", error=str(e), exc_info=True)
            return self._fallback_response()


    async def _call_chat_completion(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        max_tokens: int = 1000,
        expect_json: bool = True,
    ) -> str:
        model_to_use = model or settings.openai_model
        kwargs: Dict[str, Any] = {
            "model": model_to_use,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if expect_json:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = await self.client.chat.completions.create(**kwargs)
            if not response.choices:
                return ""
            message = response.choices[0].message
            if not message:
                return ""
            content = message.content
            if isinstance(content, list):
                text_parts: List[str] = []
                for part in content:
                    if isinstance(part, dict):
                        text_parts.append(str(part.get("text", "")))
                content = "".join(text_parts)
            return content or ""
        except openai.BadRequestError as error:
            if model_to_use != "gpt-4o-mini":
                self.logger.warning(
                    "Chat completions model unsupported, retrying fallback",
                    model=model_to_use,
                    error=str(error),
                )
                return await self._call_chat_completion(
                    messages,
                    model="gpt-4o-mini",
                    max_tokens=max_tokens,
                    expect_json=expect_json,
                )
            raise

    def _use_responses_api(self) -> bool:
        """Determine whether to call the Responses API instead of Chat Completions."""
        model_name = settings.openai_model or ""
        use_responses = model_name.startswith(("o", "gpt-4.1"))
        self.logger.debug(
            "llm_responses_api_check",
            model=model_name,
            use_responses=use_responses,
        )
        return use_responses

    def _build_responses_input(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert chat-completion style messages to Responses API input format."""
        formatted: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            formatted.append(
                {
                    "role": role,
                    "content": [
                        {
                            "type": "text",
                            "text": str(content),
                        }
                    ],
                }
            )

        self.logger.debug(
            "llm_responses_input_prepared",
            items=len(formatted),
        )
        return formatted

    def _extract_responses_content(self, response: Any) -> str:
        """Extract plain text content from Responses API result."""
        if response is None:
            self.logger.debug("llm_responses_content_absent")
            return ""

        if hasattr(response, "output_text") and response.output_text:
            text_value = str(response.output_text)
            self.logger.debug(
                "llm_responses_content_extracted",
                via="output_text",
                length=len(text_value),
            )
            return text_value

        chunks = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "output_text":
                chunks.append(str(getattr(item, "text", "")))
                continue
            for content_item in getattr(item, "content", []) or []:
                if isinstance(content_item, dict) and content_item.get("type") == "output_text":
                    chunks.append(str(content_item.get("text", "")))

        extracted = "".join(chunks)
        self.logger.debug(
            "llm_responses_content_extracted",
            via="output",
            length=len(extracted),
        )
        return extracted

    def _sanitize_json_string(self, raw: str) -> Optional[str]:
        """Normalize raw LLM output before JSON parsing."""
        if not raw:
            return None
        cleaned = raw.strip()
        if not cleaned:
            return None
        if cleaned.startswith("```"):
            cleaned = cleaned.strip()
            parts = cleaned.split("```", 1)
            if len(parts) > 1:
                cleaned = parts[1]
            cleaned = cleaned.strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
            if "```" in cleaned:
                cleaned = cleaned.rsplit("```", 1)[0].strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and start < end:
            cleaned = cleaned[start:end + 1]
        return cleaned or None

    def _try_parse_json(self, raw: str) -> Optional[Dict[str, Any]]:
        """Attempt to parse JSON, returning None on failure."""
        sanitized = self._sanitize_json_string(raw)
        if not sanitized:
            return None
        try:
            parsed = json.loads(sanitized)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
        return None

    def _build_messages(self, context: LLMContext) -> List[Dict[str, str]]:
        """Build messages for OpenAI API call."""
        messages = [
            {"role": "system", "content": self._build_system_message(context)}
        ]
        
        # Add conversation history
        for msg in context.messages_history[-10:]:  # Last 10 messages
            role = "assistant" if msg["role"] == "bot" else "user"
            messages.append({
                "role": role,
                "content": msg["text"]
            })
        
        return messages
    
    def _build_system_message(self, context: LLMContext) -> str:
        """Build comprehensive system message with context."""
        user = context.user
        
        # User profile
        profile_info = f"""
ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:
- Имя: {user.first_name or 'Не указано'} {user.last_name or ''}
- Сегмент: {user.segment or 'не определен'} ({user.lead_score} баллов)
- Этап воронки: {user.funnel_stage}
- Телефон: {'указан' if user.phone else 'не указан'}
- Email: {'указан' if user.email else 'не указан'}
"""
        
        # Survey summary
        survey_info = ""
        if context.survey_summary:
            survey_info = f"\nРЕЗУЛЬТАТЫ АНКЕТЫ:\n{context.survey_summary}"
        
        # Available materials
        materials_info = ""
        if context.candidate_materials:
            materials_info = "\nДОСТУПНЫЕ МАТЕРИАЛЫ:\n"
            for material in context.candidate_materials[:3]:
                materials_info += f"- {material.get('title', '')}: {material.get('url', '')}\n"
        
        # Relevant products
        products_info = ""
        if context.relevant_products:
            products_info = "\nРЕЛЕВАНТНЫЕ ПРОГРАММЫ:\n"
            for product in context.relevant_products[:2]:
                products_info += f"- {product.get('name', '')}: {product.get('price', '')} руб\n"
        
        scenario_block = ""
        if context.scenario_prompt:
            scenario_block = f"\nСЦЕНАРНЫЕ УКАЗАНИЯ:\n{context.scenario_prompt}\n"

        return f"""
{self.system_prompt}{scenario_block}
{profile_info}
{survey_info}
{materials_info}
{products_info}

ПОЛИТИКИ БЕЗОПАСНОСТИ: {self.safety_policies}

ЗАДАЧА: Твой ответ ДОЛЖЕН быть в формате JSON со следующими полями: "reply_text" (string), "buttons" (list of dicts with "text" and "callback"), "next_action" (string), "confidence" (float).
"""
    
    def _escalation_response(self, issues: Optional[List[SafetyIssue]] = None) -> LLMResponse:
        """Return a safe response that escalates the dialogue to a manager."""
        escalation_text = (
            "Хочу, чтобы ответ был максимально точным, поэтому подключу менеджера."
            "\n\nКоллега скоро свяжется с вами, а если хотите ускорить процесс, нажмите кнопку ниже."
        )
        buttons = [
            {"text": "📞 Позвать менеджера", "callback": Callbacks.MANAGER_REQUEST},
            {"text": "📚 Материалы", "callback": "materials:educational"},
        ]
        return LLMResponse(
            reply_text=escalation_text,
            buttons=buttons,
            next_action="escalate_to_manager",
            confidence=0.0,
            safety_issues=issues or [],
            is_safe=True,
        )

    def _fallback_response(self) -> LLMResponse:
        """Return fallback response in case of errors."""
        fallback_text = (
            "Похоже, интеллектуальный ассистент временно недоступен, но я все равно помогу.\n\n"
            "🎯 Начнем с короткой анкеты, чтобы подобрать подходящую программу, или выбери другой шаг ниже."
        )
        return LLMResponse(
            reply_text=fallback_text,
            buttons=[
                {"text": "🎯 Пройти тест", "callback": Callbacks.SURVEY_START},
                {"text": "📚 Получить материалы", "callback": "materials:educational"},
                {"text": "📞 Консультация", "callback": Callbacks.CONSULT_OFFER},
            ],
            next_action="fallback_flow",
            confidence=0.0,
            safety_issues=[],
            is_safe=True
        )
    
    async def generate_user_summary(self, context: LLMContext) -> str:
        """Generate summary of user profile and conversation."""
        try:
            summarizer_prompt = prompt_loader.load_prompt("summarizer")
            if not summarizer_prompt:
                return "Краткая сводка недоступна"
            
            messages = [
                {"role": "system", "content": summarizer_prompt},
                {"role": "user", "content": self._build_context_for_summary(context)}
            ]
            
            content = ""
            if self._use_responses_api():
                responses_input = self._build_responses_input(messages)
                try:
                    response = await self.client.responses.create(
                        model=settings.openai_model,
                        input=responses_input,
                        max_output_tokens=500,
                    )
                    content = self._extract_responses_content(response).strip()
                except Exception as api_error:
                    self.logger.warning(
                        "Responses API failed while generating summary, falling back to chat completions",
                        error=str(api_error),
                    )
                    content = ""
                if not content:
                    content = await self._call_chat_completion(
                        messages,
                        max_tokens=500,
                        expect_json=False,
                    )
            else:
                content = await self._call_chat_completion(
                    messages,
                    max_tokens=500,
                    expect_json=False,
                )
            return content.strip() or "Краткая сводка недоступна"
            
        except Exception as e:
            self.logger.error("Error generating user summary", error=str(e))
            return "Не удалось создать сводку профиля"
    
    def _build_context_for_summary(self, context: LLMContext) -> str:
        """Build context string for summary generation."""
        user = context.user
        
        context_str = f"""
Пользователь: {user.first_name} {user.last_name or ''}
Сегмент: {user.segment} ({user.lead_score} баллов)
Этап: {user.funnel_stage}

Анкета: {context.survey_summary or 'не пройдена'}

Последние сообщения:
"""
        
        for msg in context.messages_history[-5:]:
            role = "Пользователь" if msg["role"] == "user" else "Бот"
            context_str += f"{role}: {msg['text'][:100]}...\n"
        
        return context_str



