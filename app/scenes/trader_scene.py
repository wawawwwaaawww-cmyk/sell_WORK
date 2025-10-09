"""Trader scene for warm segment users (6-10 points)."""

from typing import Dict, Optional

from app.models import User, UserSegment, FunnelStage
from app.services.llm_service import LLMResponse
from .base_scene import BaseScene, SceneState, SceneResponse


class TraderScene(BaseScene):
    """Scene for intermediate users who have some experience and are ready for more advanced content."""
    
    def __init__(self, session):
        super().__init__(session)
        self.scene_name = "trader"
        # Standard confidence threshold for traders
        self.confidence_threshold = 0.5
    
    async def apply_scene_policy(
        self, 
        user: User, 
        llm_response: LLMResponse, 
        state: SceneState
    ) -> LLMResponse:
        """Apply trader scene specific policy."""
        
        # Traders can handle consultation offers earlier
        if llm_response.next_action == "educate" and state.attempts_count > 1:
            llm_response.next_action = "offer_consult"
            
        # Show success stories and case studies
        if llm_response.next_action == "show_materials":
            llm_response.next_action = "show_cases"
            
        # Balance education with actionable advice
        if llm_response.confidence > 0.7 and llm_response.next_action == "ask":
            llm_response.next_action = "provide_strategy"
            
        # Add strategic tone
        if not any(word in llm_response.reply_text.lower() for word in ["стратегия", "подход", "план"]):
            llm_response.reply_text = self._add_strategic_tone(llm_response.reply_text)
            
        return llm_response
    
    def _add_strategic_tone(self, text: str) -> str:
        """Add strategic and action-oriented tone to response."""
        strategic_endings = [
            "\n\n🎯 *Важно выбрать правильную стратегию под твои цели.*",
            "\n\n📈 *Рассмотрим конкретные подходы к достижению результата.*",
            "\n\n💡 *Следующий шаг - это составление персонального плана действий.*"
        ]
        
        # Add strategic tone if not present
        if not any(phrase in text for phrase in ["стратегия", "план", "подход", "система"]):
            import random
            text += random.choice(strategic_endings)
            
        return text
    
    async def determine_next_scene(
        self, 
        user: User, 
        llm_response: LLMResponse, 
        state: SceneState
    ) -> Optional[str]:
        """Determine if should transition to another scene."""
        
        # If user shows high engagement and readiness, move to investor scene
        if (
            llm_response.confidence > 0.8 and 
            llm_response.next_action in ["offer_consult", "offer_payment"] and
            user.lead_score > 8
        ):
            return "investor"
        
        # If user needs basic education, move back to newbie
        if (
            llm_response.confidence < 0.3 and 
            state.attempts_count > 2
        ):
            return "newbie"
            
        # Stay in trader scene for continued development
        return None
    
    def get_scene_prompts(self) -> Dict[str, str]:
        """Get trader scene specific prompts."""
        return {
            "system_addition": """
СЦЕНАРИЙ: РАЗВИВАЮЩИЙСЯ ТРЕЙДЕР

Пользователь имеет базовые знания в криптовалютах (6-10 баллов). Готов к более серьёзному подходу.

ТВОЯ РОЛЬ:
- Стратегический консультант
- Показываешь конкретные возможности
- Фокусируешься на развитии навыков
- Мотивируешь на действия

ПОДХОД К ПОЛЬЗОВАТЕЛЮ:
1. Признавай его опыт
2. Предлагай следующий уровень развития
3. Показывай реальные кейсы успеха
4. Направляй к конкретным действиям

ЛОГИКА ДЕЙСТВИЙ:
- provide_strategy: дай стратегические советы
- show_cases: покажи кейсы успеха
- offer_consult: предложи консультацию
- escalate_to_expert: направь к эксперту

Можешь предлагать более продвинутые материалы и консультации.
""",
            "tone": "профессиональный, мотивирующий, стратегический",
            "keywords": ["стратегия", "развитие", "возможности", "результат", "план"]
        }
    
    async def create_strategy_response(self, strategy_focus: str, user: User) -> SceneResponse:
        """Create strategic response based on focus area."""
        
        strategy_responses = {
            "growth": {
                "message": """📈 **Стратегии роста для амбициозных трейдеров**

**Твой уровень позволяет рассматривать:**

🎯 **Краткосрочные стратегии:**
• Технический анализ и торговля по трендам
• Скальпинг на волатильности
• Арбитраж между биржами

📊 **Среднесрочные подходы:**
• Swing trading по недельным циклам
• Сезонные паттерны в криптовалютах
• Портфельная торговля топ-10 монет

🚀 **Продвинутые техники:**
• Фундаментальный анализ проектов
• DeFi фарминг и стейкинг
• NFT и новые секторы

**Какое направление интересует больше всего?** 🤔""",
                "buttons": [
                    {"text": "📊 Технический анализ", "callback_data": "strategy:technical"},
                    {"text": "🚀 DeFi и новые возможности", "callback_data": "strategy:defi"},
                    {"text": "📞 Персональная стратегия", "callback_data": "consult:strategy"},
                    {"text": "📚 Кейсы успешных трейдеров", "callback_data": "materials:cases"}
                ]
            },
            "portfolio": {
                "message": """💼 **Портфельные стратегии для устойчивого роста**

**Рекомендации для твоего уровня:**

🎯 **Базовый портфель (70%):**
• Bitcoin (40%) - основа стабильности
• Ethereum (30%) - экосистема и рост

📈 **Ростовая часть (20%):**
• Топ-10 альткоинов по капитализации
• Ротация по секторам (DeFi, GameFi, Layer 2)

🚀 **Спекулятивная часть (10%):**
• Новые перспективные проекты
• ICO и токен-сейлы

**Принципы управления:**
✅ Ребалансировка раз в месяц
✅ Фиксация прибыли по уровням
✅ Стоп-лоссы для защиты капитала

**Хочешь персональный разбор твоего портфеля?** 🤝""",
                "buttons": [
                    {"text": "📊 Анализ моего портфеля", "callback_data": "consult:portfolio"},
                    {"text": "📈 Стратегии ребалансировки", "callback_data": "materials:rebalancing"},
                    {"text": "🎯 Расчёт оптимального риска", "callback_data": "survey:risk"},
                    {"text": "💬 Обсудить с экспертом", "callback_data": "consult:expert"}
                ]
            }
        }
        
        response_data = strategy_responses.get(strategy_focus, strategy_responses["growth"])
        
        return SceneResponse(
            message_text=response_data["message"],
            buttons=response_data["buttons"],
            log_event={
                "scene": self.scene_name,
                "action": "strategy_provided",
                "focus": strategy_focus
            }
        )
    
    async def show_success_cases(self, case_type: str, user: User) -> SceneResponse:
        """Show relevant success cases for traders."""
        
        cases = {
            "technical": """📊 **Кейс: Технический анализ в действии**

**Участник курса - Алексей, 29 лет:**
*Изначальный капитал: 200 000 рублей*

**Стратегия:**
• Изучил паттерны технического анализа
• Торговал 3-4 сделки в неделю
• Использовал стоп-лоссы и тейк-профиты

**Результат за 6 месяцев:**
✅ +180% к депозиту (560 000 рублей)
✅ 73% прибыльных сделок
✅ Средняя прибыль за сделку: 8%

**Ключевые факторы успеха:**
🎯 Строгая дисциплина входов/выходов
📚 Постоянное изучение новых паттернов  
💪 Эмоциональный контроль

*"Главное - не торопиться и следовать системе"* - Алексей

**Готов изучить его подход?** 🚀""",
            
            "portfolio": """💼 **Кейс: Портфельная стратегия**

**Участница курса - Мария, 35 лет:**
*Изначальный капитал: 500 000 рублей*

**Стратегия:**
• Портфельный подход с ребалансировкой
• 60% топ-криптовалюты, 40% альткоины
• Месячные корректировки

**Результат за 8 месяцев:**
✅ +240% к портфелю (1 700 000 рублей)
✅ Максимальная просадка: всего 15%
✅ Стабильный рост без стресса

**Секреты успеха:**
📊 Детальная аналитика каждый месяц
🎯 Чёткие цели по фиксации прибыли
😌 Спокойное отношение к волатильности

*"Система работает, если ей следовать"* - Мария

**Хочешь такой же подход?** 💎"""
        }
        
        case_text = cases.get(case_type, cases["technical"])
        
        return SceneResponse(
            message_text=case_text,
            buttons=[
                {"text": "📚 Изучить эту стратегию", "callback_data": f"materials:{case_type}"},
                {"text": "📞 Консультация по методике", "callback_data": f"consult:{case_type}"},
                {"text": "💡 Другие кейсы успеха", "callback_data": "materials:all_cases"},
                {"text": "🎯 Начать обучение", "callback_data": "offer:course"}
            ],
            log_event={
                "scene": self.scene_name,
                "action": "case_shown",
                "case_type": case_type
            }
        )