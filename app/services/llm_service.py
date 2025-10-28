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
from app.services.logging_service import ConversationLoggingService
from app.repositories.user_repository import UserRepository
 
LOW_CONFIDENCE_THRESHOLD = 0.35



@dataclass
class LLMContext:
    """Context data for LLM requests."""
    user: User
    messages_history: List[Dict[str, str]]
    candidate_materials: List[Dict[str, Any]] = None
    relevant_products: List[Dict[str, Any]] = None
    funnel_stage: str = "new"
    scenario_prompt: Optional[str] = None
    active_function: Optional[str] = None
    recent_messages: Optional[List[Dict[str, Any]]] = None
    conversation_pairs: Optional[List[Dict[str, str]]] = None
    product_focus: Optional[Dict[str, Any]] = None


@dataclass 
class LLMResponse:
    """Structured LLM response."""
    reply_text: str
    buttons: List[Dict[str, str]]
    next_action: str
    confidence: float
    safety_issues: List[SafetyIssue]
    is_safe: bool
    intent: Optional[str] = None
    need_reask: bool = False


async def get_embedding(text: str, model: str = "text-embedding-3-small") -> Optional[List[float]]:
    """Generates an embedding for a given text."""
    if not text:
        return None
    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        response = await client.embeddings.create(input=[text], model=model)
        return response.data[0].embedding
    except Exception as e:
        structlog.get_logger().error("Failed to get embedding", error=str(e))
        return None


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
    
    def __init__(self, session: Optional[Any] = None, user: Optional[User] = None):
        self.session = session
        self.user = user
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.policy_layer = PolicyLayer()
        self.safety_validator = SafetyValidator()
        self.logger = structlog.get_logger()

        # Load system prompts
        self.system_prompt = prompt_loader.get_system_prompt()
        self.safety_policies = prompt_loader.get_safety_policies()
        self.sales_methodology = prompt_loader.get_sales_methodology()
        self.persona_prompt = prompt_loader.load_prompt("system_manager") or ""
        followups_prompt = prompt_loader.load_prompt("followups") or ""
        self.dialog_analysis_guidelines = self._extract_guideline_section(
            followups_prompt,
            header="АНАЛИЗ СООБЩЕНИЙ",
        )
    
    async def generate_response(self, context: LLMContext) -> LLMResponse:
        """Generate LLM response with safety and policy validation."""
        try:
            if not settings.openai_api_key:
                self.logger.warning("OpenAI API key is not configured; using fallback response")
                return self._fallback_response()
            messages = self._build_messages(context)

            if self._use_responses_api():
                raw_content = await self._call_responses_api(messages)
                if not raw_content:
                    self.logger.info(
                        "Responses API returned empty result, falling back to chat completions",
                        model=settings.openai_model,
                    )
                    raw_content = await self._call_chat_completion(messages)
            else:
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
                is_safe=is_safe,
                intent=llm_response.get("intent"),
                need_reask=llm_response.get("need_reask", False),
            )

        except Exception as e:
            self.logger.error("LLM service error", error=str(e), exc_info=True)
            return self._fallback_response()

    async def get_completion(
        self,
        messages: List[Dict[str, Any]],
        purpose: str = "generic",
        max_tokens: int = 200,
        expect_json: bool = False,
    ) -> str:
        """
        Generates a text completion for a given list of messages using the most suitable model.
        This is a simplified public method for direct LLM calls without full context.
        """
        self.logger.info("Generating completion", purpose=purpose)
        try:
            # For specific, controlled tasks, a smaller and faster model might be sufficient.
            model = settings.openai_model
            if purpose in ("onboarding_followup", "summarizer"):
                model = "gpt-4o-mini"

            return await self._call_chat_completion(
                messages,
                model=model,
                max_tokens=max_tokens,
                expect_json=expect_json,
            )
        except Exception:
            self.logger.exception("Failed to get completion", purpose=purpose)
            return ""

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
        }
        kwargs["max_completion_tokens"] = max_tokens
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
        except Exception as error:
            self.logger.error(
                "Chat completions API call failed",
                model=model_to_use,
                error=str(error),
            )
            raise

    async def _call_responses_api(
        self,
        messages: List[Dict[str, Any]],
        *,
        max_tokens: int = 1000,
        expect_json: bool = True,
    ) -> str:
        """Call the Responses API and return raw string content."""
        formatted_messages = self._build_responses_input(messages)
        kwargs: Dict[str, Any] = {
            "model": settings.openai_model,
            "input": formatted_messages,
            "max_output_tokens": max_tokens,
        }
        if expect_json:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = await self.client.responses.create(**kwargs)
            return self._extract_responses_content(response)
        except openai.BadRequestError as error:
            self.logger.warning(
                "Responses API rejected request, attempting chat completions fallback",
                model=settings.openai_model,
                error=str(error),
            )
            return ""
        except Exception as error:
            self.logger.error(
                "Responses API call failed",
                model=settings.openai_model,
                error=str(error),
            )
            return ""

    def _use_responses_api(self) -> bool:
        """Determine whether to call the Responses API instead of Chat Completions."""
        model_name = settings.openai_model or ""
        use_responses = model_name.startswith(("o", "gpt-4.1", "gpt-5"))
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

    def _extract_guideline_section(self, prompt_text: str, header: str) -> str:
        """Extract specific guideline section from prompt text with logging."""

        has_prompt = bool(prompt_text)
        self.logger.info(
            "guideline_extraction_started",
            header=header,
            has_prompt=has_prompt,
        )

        if not has_prompt:
            self.logger.warning(
                "guideline_prompt_missing",
                header=header,
            )
            return ""

        lines = prompt_text.splitlines()
        header_upper = header.strip().upper()
        capture = False
        collected: List[str] = []

        for line in lines:
            stripped = line.strip()
            if not capture:
                if stripped.upper().startswith(header_upper):
                    capture = True
                    self.logger.debug(
                        "guideline_section_detected",
                        header=header,
                        line=stripped,
                    )
                    continue
            else:
                if (
                    stripped
                    and stripped == stripped.upper()
                    and stripped.endswith(":")
                    and stripped.upper() != header_upper
                ):
                    break
                collected.append(line.rstrip())

        extracted = "\n".join(collected).strip()

        self.logger.info(
            "guideline_extraction_completed",
            header=header,
            length=len(extracted),
        )

        return extracted

    def _build_system_message(self, context: LLMContext) -> str:
        """Build comprehensive system message with context."""
        user = context.user

        # User profile
        knowledge_level_map = {
            UserSegment.COLD: "новичок",
            UserSegment.WARM: "продвинутый",
            UserSegment.HOT: "эксперт",
        }

        knowledge_level = knowledge_level_map.get(user.segment, "не определён")

        profile_info = f"""
ПРОФИЛЬ ПОЛЬЗОВАТЕЛЯ:
- Имя: {user.first_name or 'Не указано'} {user.last_name or ''}
- Сегмент: {user.segment or 'не определен'} ({user.lead_score} баллов)
- Уровень знаний: {knowledge_level}
- Этап воронки: {user.funnel_stage}
- Телефон: {'указан' if user.phone else 'не указан'}
- Email: {'указан' if user.email else 'не указан'}
"""
        
        # Survey summary
        survey_info = ""
        
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

        persona_block = ""
        if self.persona_prompt:
            persona_block = f"\nПЕРСОНАЖ И СТИЛЬ:\n{self.persona_prompt}\n"

        dialogue_guidelines_block = ""
        if self.dialog_analysis_guidelines:
            dialogue_guidelines_block = (
                "\nАНАЛИЗ ДИАЛОГА (из подсказки FOLLOW-UPS):\n"
                f"{self.dialog_analysis_guidelines}\n"
                "Следуй этим принципам, чтобы понимать ответы пользователя и реагировать по смыслу. "
                "Если он отвечает по существу твоего вопроса (даже другими словами или синонимами), продолжай диалог в той же логике. "
                "Если пользователь меняет тему или задает новый вопрос, переключайся на новый смысл и отвечай, опираясь на последние пять сообщений и общий контекст ниже."
            )
        else:
            dialogue_guidelines_block = (
                "\nАНАЛИЗ ДИАЛОГА:\n"
                "Внимательно анализируй последние пять сообщений и отвечай по смыслу. "
                "Сохраняй текущую тему, если пользователь дал релевантный ответ, и переключайся, если тема изменилась."
            )

        recent_messages_block = ""
        if context.recent_messages:
            formatted = []
            for msg in context.recent_messages:
                role = msg.get("role")
                text = msg.get("text", "").replace("\n", " ")
                timestamp = msg.get("timestamp") or msg.get("created_at")
                if hasattr(timestamp, "isoformat"):
                    timestamp = timestamp.isoformat()
                formatted.append(f"- {role}: {text[:400]} ({timestamp})")
            recent_messages_block = "\nПОСЛЕДНИЕ 5 СООБЩЕНИЙ:\n" + "\n".join(formatted)

        qa_block = ""
        if context.conversation_pairs:
            pairs = []
            for idx, pair in enumerate(context.conversation_pairs, start=1):
                user_q = pair.get("user", "").replace("\n", " ")
                bot_a = pair.get("bot", "").replace("\n", " ")
                pairs.append(f"{idx}. Вопрос: {user_q[:400]}\n   Ответ: {bot_a[:400]}")
            qa_block = "\nИСТОРИЯ Q/A:\n" + "\n".join(pairs)

        active_function_block = ""
        if context.active_function:
            active_function_block = f"\nАКТИВНАЯ ФУНКЦИЯ БОТА: {context.active_function}"

        product_focus_block = ""
        if context.product_focus:
            product = context.product_focus
            product_focus_block = (
                "\nТЕКУЩИЙ ПРОДУКТ К ПРОДАЖЕ: "
                f"{product.get('name', 'Без названия')} — цена {product.get('price', 'N/A')}"
                f". Описание: {product.get('description', '')[:400]}"
            )

        return f"""
{self.system_prompt}{scenario_block}{persona_block}{dialogue_guidelines_block}
{profile_info}
{survey_info}
{materials_info}
{products_info}
{recent_messages_block}
{qa_block}
{active_function_block}
{product_focus_block}

ПРОДАЖНАЯ МЕТОДОЛОГИЯ: {self.sales_methodology}
ПОЛИТИКИ БЕЗОПАСНОСТИ: {self.safety_policies}

ЗАДАЧА: Твой ответ ДОЛЖЕН быть в формате JSON со следующими полями: "answer" (string), "intent" (string), "next_action" (string), "stage_transition" (string), "need_reask" (boolean), "confidence" (float).
ПРАВИЛО Answer-First: Сначала ответь на смысл последней реплики. Не повторяй свой предыдущий вопрос, если пользователь пишет о другом.
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
                {"text": "🎯 Пройти тест", "callback": "survey_start"},
                {"text": "📚 Получить материалы", "callback": "materials:educational"},
                {"text": "📞 Консультация", "callback": "consult_offer"},
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

Анкета: не пройдена

Последние сообщения:
"""
        
        for msg in context.messages_history[-5:]:
            role = "Пользователь" if msg["role"] == "user" else "Бот"
            context_str += f"{role}: {msg['text'][:100]}...\n"
        
        return context_str


    async def validate_script_relevance(
        self, user_query: str, candidates: List[Dict[str, Any]]
    ) -> (bool, Optional[str]):
        """
        Uses an LLM to validate if any of the candidate answers are relevant to the user query.
        """
        if not candidates:
            return False, None

        prompt = self._build_validation_prompt(user_query, candidates)
        messages = [{"role": "system", "content": prompt}]

        try:
            raw_response = await self._call_chat_completion(
                messages, model=settings.judge_model, max_tokens=200, expect_json=True
            )
            result = self._try_parse_json(raw_response)

            if not result or not isinstance(result, dict):
                self.logger.warning("LLM validation returned invalid JSON.", response=raw_response)
                return False, None

            is_relevant = result.get("is_relevant", False)
            best_id = result.get("best_id")

            if is_relevant and best_id is not None:
                for candidate in candidates:
                    if candidate["id"] == best_id:
                        # Final safety check on the answer from the script
                        sanitized_text, _ = self.safety_validator.validate_response(candidate["answer"])
                        return True, sanitized_text

        except Exception:
            self.logger.exception("Error during LLM validation of script relevance.")

        return False, None

    def _build_validation_prompt(self, user_query: str, candidates: List[Dict[str, Any]]) -> str:
        """Builds the prompt for the LLM judge."""
        prompt = (
            "You are a validation expert. Your task is to determine if any of the provided answers "
            "are a relevant and helpful response to the user's query. "
            "Respond in JSON format with 'is_relevant' (boolean) and 'best_id' (integer ID of the best answer if relevant).\n\n"
            f"User Query: \"{user_query}\"\n\n"
            "Candidate Answers:\n"
        )
        for cand in candidates:
            prompt += (
                f"- ID: {cand['id']}\n"
                f"  - Matched Message: \"{cand['message']}\"\n"
                f"  - Proposed Answer: \"{cand['answer']}\"\n"
                f"  - Similarity Score: {cand['similarity']:.4f}\n\n"
            )
        prompt += (
            "Criteria for relevance:\n"
            "1. The answer must directly address the user's intent.\n"
            "2. The answer must be factually consistent with the user's query.\n"
            "3. Ignore answers that are only vaguely related.\n\n"
            "Your JSON response:"
        )
        return prompt

    async def get_response(self, text: str, user_id: int) -> str:
        """
        Generates a response from the LLM for a given text message and user.
        This is a simplified method for direct dialog handling.
        """
        if not self.session:
            self.logger.error("LLMService.get_response called without a session.")
            return "Не могу сейчас ответить, технические неполадки."

        try:
            user_repo = UserRepository(self.session)
            user = await user_repo.get_user_by_id(user_id)
            if not user:
                self.logger.error("User not found for LLM response.", user_id=user_id)
                return "Произошла ошибка, пользователь не найден."

            logging_service = ConversationLoggingService(self.session)
            history = await logging_service.get_last_messages(user_id, limit=10)
            
            # Add current user message to history for context
            history.append({"role": "user", "text": text})

            context = LLMContext(user=user, messages_history=history)
            
            messages = self._build_messages(context)
            
            # We expect a simple text response here, not a JSON object
            raw_response = await self._call_chat_completion(
                messages, max_tokens=1000, expect_json=False
            )

            sanitized_text, _ = self.safety_validator.validate_response(raw_response)

            return sanitized_text or "Не могу сейчас ответить."

        except Exception as e:
            self.logger.error("Failed to get LLM response in get_response", exc_info=True, user_id=user_id)
            return "Возникла проблема при обработке вашего запроса. Попробуйте позже."
