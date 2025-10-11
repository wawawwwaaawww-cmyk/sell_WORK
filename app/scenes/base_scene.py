"""Base scene class for all conversation scenarios."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, MessageRole
from app.services.llm_service import LLMService, LLMContext, LLMResponse
from app.services.materials_service import MaterialService
from app.services.payment_service import PaymentService
from app.safety.validator import SafetyValidator


@dataclass
class SceneState:
    """Scene state for tracking dialogue progress."""
    current_step: str = "initial"
    attempts_count: int = 0
    confidence_history: List[float] = field(default_factory=list)
    context_data: Dict[str, Any] = field(default_factory=dict)
    last_action: str = "none"
    escalation_triggered: bool = False


@dataclass
class SceneResponse:
    """Response from scene processing."""
    message_text: str
    buttons: List[Dict[str, str]] = field(default_factory=list)
    next_scene: Optional[str] = None
    escalate: bool = False
    log_event: Optional[Dict[str, Any]] = None
    update_user_stage: Optional[str] = None


class BaseScene(ABC):
    """Base class for all conversation scenarios."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.llm_service = LLMService()
        self.materials_service = MaterialService(session)
        self.payment_service = PaymentService(session)
        self.safety_validator = SafetyValidator()
        self.logger = structlog.get_logger()
        
        # Scene configuration
        self.scene_name = self.__class__.__name__.lower().replace('scene', '')
        self.max_attempts = 3
        self.confidence_threshold = 0.5
        self.escalation_keywords = [
            "не понял", "не понимаю", "сложно",
            "менеджер", "человек", "оператор",
            "не ясно", "непонятно", "помощь"
        ]
    
    async def process_message(
        self, 
        user: User, 
        message_text: str, 
        state: SceneState
    ) -> SceneResponse:
        """Process incoming message and generate response."""
        try:
            # Update attempt counter
            state.attempts_count += 1
            
            # Check for escalation triggers
            if self.should_escalate(user, message_text, state):
                return await self._create_escalation_response(user, state)
            
            # Build LLM context
            context = await self._build_llm_context(user, message_text, state)
            
            # Generate response using LLM
            llm_response = await self.llm_service.generate_response(context)
            
            # Apply scene-specific policy
            processed_response = await self.apply_scene_policy(
                user, llm_response, state
            )
            
            # Update state
            state.confidence_history.append(llm_response.confidence)
            state.last_action = llm_response.next_action
            
            # Check if should transition to another scene
            next_scene = await self.determine_next_scene(
                user, llm_response, state
            )
            
            # Log interaction
            await self._log_interaction(user, message_text, processed_response)
            
            return SceneResponse(
                message_text=processed_response.reply_text,
                buttons=processed_response.buttons,
                next_scene=next_scene,
                escalate=processed_response.next_action == "escalate_to_manager",
                log_event={
                    "scene": self.scene_name,
                    "action": processed_response.next_action,
                    "confidence": llm_response.confidence
                }
            )
            
        except Exception as e:
            try:
                await self.session.rollback()
            except Exception as rollback_error:  # pragma: no cover - best effort cleanup
                self.logger.warning(
                    "Failed to rollback session after scene error",
                    scene=self.scene_name,
                    user_id=user.id,
                    rollback_error=str(rollback_error)
                )

            self.logger.error(
                "Scene processing error",
                scene=self.scene_name,
                user_id=user.id,
                error=str(e),
                exc_info=True
            )
            return await self._create_fallback_response(user)
    
    def should_escalate(
        self, 
        user: User, 
        message_text: str, 
        state: SceneState
    ) -> bool:
        """Determine if conversation should be escalated to manager."""
        
        # Check escalation keywords
        message_lower = message_text.lower()
        if any(keyword in message_lower for keyword in self.escalation_keywords):
            return True
        
        # Check low confidence pattern
        if len(state.confidence_history) >= 3:
            recent_confidences = state.confidence_history[-3:]
            if all(conf < self.confidence_threshold for conf in recent_confidences):
                return True
        
        # Check repeated attempts without progress
        if state.attempts_count > self.max_attempts:
            return True
        
        # Check if already triggered escalation
        if state.escalation_triggered:
            return True
        
        return False
    
    async def _build_llm_context(
        self,
        user: User,
        message_text: str,
        state: SceneState,
    ) -> LLMContext:
        """Build context for LLM request using the current scene state."""
        
        # Get conversation history (last 10 messages)
        messages_history = await self._get_conversation_history(user)

        if message_text:
            messages_history.append({
                "role": "user",
                "text": message_text,
                "created_at": datetime.utcnow().isoformat(),
                "meta": {"source": "live_input"}
            })

        recent_messages = self._extract_recent_messages(messages_history)
        qa_pairs = self._build_question_answer_pairs(messages_history)
        active_function = self._compose_active_function_label(state)

        # Get relevant materials
        materials = await self.materials_service.get_materials_for_segment(
            segment=user.segment or "cold",
            funnel_stage=user.funnel_stage,
            limit=3
        )

        # Get relevant products
        products = await self.payment_service.get_suitable_products(user)
        product_payload = [
            {
                "name": product.name,
                "code": product.code,
                "price": str(product.price),
                "description": product.description or "",
                "is_active": product.is_active,
            }
            for product in products[:3]
        ]

        # Get survey summary if available
        survey_summary = await self._get_survey_summary(user)

        product_focus = product_payload[0] if product_payload else None

        self.logger.info(
            "llm_context_built",
            user_id=user.id,
            scene=self.scene_name,
            active_function=active_function,
            materials=len(materials),
            products=len(product_payload),
        )

        return LLMContext(
            user=user,
            messages_history=messages_history,
            survey_summary=survey_summary,
            candidate_materials=[
                {
                    "title": mat.title,
                    "type": mat.type,
                    "summary": mat.body[:200] + "..." if mat.body else ""
                }
                for mat in materials
            ],
            relevant_products=product_payload,
            funnel_stage=user.funnel_stage,
            active_function=active_function,
            recent_messages=recent_messages,
            conversation_pairs=qa_pairs,
            product_focus=product_focus,
        )

    def _extract_recent_messages(self, history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return last five messages from history for prompt context."""

        recent = history[-5:] if history else []
        self.logger.info(
            "recent_messages_prepared",
            scene=self.scene_name,
            count=len(recent),
        )
        return recent

    def _build_question_answer_pairs(self, history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        """Build up to five question-answer pairs from conversation history."""

        pairs: List[Dict[str, str]] = []
        pending_user: Optional[str] = None

        for message in history:
            role = message.get("role")
            text = str(message.get("text", ""))
            if role == "user":
                pending_user = text
            elif role == "bot" and pending_user:
                pairs.append({"user": pending_user, "bot": text})
                pending_user = None

        trimmed_pairs = pairs[-5:]
        self.logger.info(
            "qa_pairs_prepared",
            scene=self.scene_name,
            count=len(trimmed_pairs),
        )
        return trimmed_pairs

    def _compose_active_function_label(self, state: SceneState) -> str:
        """Compose label describing current function/scene for prompts."""

        parts = [f"scene:{self.scene_name}"]
        if state.current_step and state.current_step != "initial":
            parts.append(f"step:{state.current_step}")

        label = "|".join(parts)
        self.logger.info(
            "active_function_determined",
            scene=self.scene_name,
            label=label,
        )
        return label
    
    async def _get_conversation_history(self, user: User) -> List[Dict[str, str]]:
        """Get recent conversation history."""
        from app.models import Message
        from sqlalchemy import select
        
        stmt = select(Message).where(
            Message.user_id == user.id
        ).order_by(Message.created_at.desc()).limit(10)
        
        result = await self.session.execute(stmt)
        messages = result.scalars().all()
        
        return [
            {
                "role": msg.role,
                "text": msg.text,
                "created_at": msg.created_at.isoformat(),
                "meta": msg.meta or {}
            }
            for msg in reversed(messages)
        ]
    
    async def _get_survey_summary(self, user: User) -> Optional[str]:
        """Get user's survey summary."""
        from app.models import SurveyAnswer
        from sqlalchemy import select
        
        stmt = select(SurveyAnswer).where(
            SurveyAnswer.user_id == user.id
        ).order_by(SurveyAnswer.created_at)
        
        result = await self.session.execute(stmt)
        answers = result.scalars().all()
        
        if not answers:
            return None
        
        summary_parts = []
        total_score = sum(answer.points for answer in answers)
        
        summary_parts.append(f"Общий скор: {total_score} баллов")
        summary_parts.append(
            f"Ответы: {', '.join([f'{a.question_code}:{a.answer_code}' for a in answers])}"
        )
        
        return "; ".join(summary_parts)
    
    async def _log_interaction(
        self, 
        user: User, 
        message_text: str, 
        response: LLMResponse
    ):
        """Log interaction for analytics."""
        from app.models import Event
        
        event = Event(
            user_id=user.id,
            type=f"scene_{self.scene_name}_interaction",
            payload={
                "user_message": message_text,
                "bot_response_length": len(response.reply_text),
                "next_action": response.next_action,
                "confidence": response.confidence,
                "safety_issues": len(response.safety_issues)
            }
        )
        
        self.session.add(event)
        await self.session.flush()
    
    async def _create_escalation_response(self, user: User, state: SceneState) -> SceneResponse:
        """Create response for escalation to manager."""
        escalation_message = """👤 Понял, мне нужно соединить вас с нашим экспертом.

Он сможет дать вам более детальную консультацию по вашему вопросу.

Одну минутку, пожалуйста..."""
        
        return SceneResponse(
            message_text=escalation_message,
            buttons=[],
            escalate=True,
            log_event={
                "scene": self.scene_name,
                "action": "escalated",
                "reason": "auto_escalation"
            }
        )
    
    async def _create_fallback_response(self, user: User) -> SceneResponse:
        """Create fallback response for errors."""
        fallback_message = """😔 Извините, у меня возникли технические сложности.

Можете повторить свой вопрос или обратиться к нашему менеджеру."""
        
        return SceneResponse(
            message_text=fallback_message,
            buttons=[
                {"text": "👤 Менеджер", "callback_data": "manager:request"},
                {"text": "🔄 Попробовать снова", "callback_data": "retry"}
            ],
            log_event={
                "scene": self.scene_name,
                "action": "fallback",
                "reason": "processing_error"
            }
        )
    
    # Abstract methods that must be implemented by subclasses
    
    @abstractmethod
    async def apply_scene_policy(
        self, 
        user: User, 
        llm_response: LLMResponse, 
        state: SceneState
    ) -> LLMResponse:
        """Apply scene-specific policy to LLM response."""
        pass
    
    @abstractmethod
    async def determine_next_scene(
        self, 
        user: User, 
        llm_response: LLMResponse, 
        state: SceneState
    ) -> Optional[str]:
        """Determine if should transition to another scene."""
        pass
    
    @abstractmethod
    def get_scene_prompts(self) -> Dict[str, str]:
        """Get scene-specific prompts."""
        pass
