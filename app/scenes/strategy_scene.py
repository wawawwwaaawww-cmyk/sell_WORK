"""Strategy scene for initial user segmentation and strategy selection."""

from typing import Dict, Optional

from app.models import User, UserSegment, FunnelStage
from app.services.llm_service import LLMResponse
from .base_scene import BaseScene, SceneState, SceneResponse


class StrategyScene(BaseScene):
    """Scene for strategy selection and initial user engagement."""
    
    def __init__(self, session):
        super().__init__(session)
        self.scene_name = "strategy"
    
    async def apply_scene_policy(
        self, 
        user: User, 
        llm_response: LLMResponse, 
        state: SceneState
    ) -> LLMResponse:
        """Apply strategy scene specific policy."""
        
        # Strategy scene focuses on guiding users to make a choice
        if llm_response.next_action == "offer_payment":
            # Too early for payment in strategy scene
            llm_response.next_action = "ask_preferences"
            
        # If user seems confused, guide them to survey
        if llm_response.confidence < 0.6:
            llm_response.next_action = "guide_to_survey"
            
        # Ensure we're building towards a decision
        if state.attempts_count > 2 and llm_response.next_action == "ask":
            llm_response.next_action = "push_decision"
            
        return llm_response
    
    async def determine_next_scene(
        self, 
        user: User, 
        llm_response: LLMResponse, 
        state: SceneState
    ) -> Optional[str]:
        """Determine if should transition to another scene."""
        
        # If user has chosen a strategy and has segment, move to appropriate scene
        if user.segment and llm_response.next_action in ["offer_consult", "show_materials"]:
            if user.segment == UserSegment.COLD:
                return "newbie"
            elif user.segment == UserSegment.WARM:
                return "trader"
            elif user.segment == UserSegment.HOT:
                return "investor"
        
        # If user completed survey, determine scene based on score
        if user.lead_score > 0:
            if user.lead_score <= 5:
                return "newbie"
            elif user.lead_score <= 10:
                return "trader"
            else:
                return "investor"
        
        # Stay in strategy scene for continued guidance
        return None
    
    def get_scene_prompts(self) -> Dict[str, str]:
        """Get strategy scene specific prompts."""
        return {
            "system_addition": """
СЦЕНАРИЙ: ВЫБОР СТРАТЕГИИ

Ты помогаешь пользователю выбрать подходящую стратегию инвестирования в криптовалюты.

ТВОЯ ЦЕЛЬ:
- Объяснить разницу между стратегией надёжности и роста
- Помочь пользователю сделать осознанный выбор
- Мотивировать на прохождение анкеты для точного подбора программы

ДОСТУПНЫЕ СТРАТЕГИИ:
1. НАДЁЖНОСТЬ - для консервативных инвесторов, фокус на стабильности
2. РОСТ - для активных трейдеров, фокус на высокой доходности

ЛОГИКА ОТВЕТОВ:
- ask_preferences: узнай предпочтения пользователя
- guide_to_survey: направь на прохождение анкеты
- push_decision: мягко подтолкни к выбору
- show_materials: покажи релевантные материалы

НЕ предлагай платные услуги на этом этапе.
""",
            "examples": """
Пример хорошего ответа:
"🎯 Понимаю, выбор стратегии - важное решение!

Если коротко:
🛡 **Надёжность** - подходит тем, кто ценит стабильность и готов к долгосрочным инвестициям
🚀 **Рост** - для тех, кто готов активно изучать рынок и принимать риски ради высокой доходности

Какой подход тебе ближе по духу? 🤔"
"""
        }
    
    async def get_strategy_specific_materials(self, strategy_type: str, user: User):
        """Get materials specific to chosen strategy."""
        if strategy_type == "safety":
            return await self.materials_service.get_materials_by_context(
                context="safety_concerns",
                segment=user.segment or UserSegment.COLD,
                limit=3
            )
        elif strategy_type == "growth":
            return await self.materials_service.get_materials_by_context(
                context="advanced_strategies", 
                segment=user.segment or UserSegment.WARM,
                limit=3
            )
        
        return []
    
    async def create_strategy_response(
        self, 
        strategy_type: str, 
        user: User
    ) -> SceneResponse:
        """Create response for specific strategy selection."""
        
        if strategy_type == "safety":
            message = """🛡 **Отличный выбор! Стратегия надёжности - это мудрый подход.**

**Что тебя ждёт:**
✅ Изучение основ безопасного инвестирования
✅ Топовые криптовалюты с наименьшими рисками  
✅ Стейкинг для пассивного дохода
✅ Долгосрочные стратегии накопления

💡 *Эта стратегия поможет безопасно войти в криптомир и постепенно наращивать капитал.*

Хочешь узнать больше о программах обучения? 📚"""
            
            buttons = [
                {"text": "📊 Пройти тест для подбора программы", "callback_data": "survey:start"},
                {"text": "📞 Записаться на консультацию", "callback_data": "consult:offer"},
                {"text": "📚 Показать материалы", "callback_data": "materials:safety"}
            ]
            
        else:  # growth strategy
            message = """🚀 **Амбициозный выбор! Стратегия роста для целеустремлённых.**

**Что тебя ждёт:**
✅ Технический и фундаментальный анализ
✅ Поиск перспективных проектов
✅ Активные торговые стратегии
✅ Управление высокодоходным портфелем

⚠️ *Помни: высокая доходность требует глубоких знаний и готовности к рискам.*

Готов погрузиться в активную торговлю? 📈"""
            
            buttons = [
                {"text": "📊 Пройти тест для подбора программы", "callback_data": "survey:start"},
                {"text": "📞 Записаться на консультацию", "callback_data": "consult:offer"},
                {"text": "📚 Показать кейсы роста", "callback_data": "materials:growth"}
            ]
        
        return SceneResponse(
            message_text=message,
            buttons=buttons,
            log_event={
                "scene": self.scene_name,
                "action": "strategy_selected",
                "strategy": strategy_type
            }
        )