"""Newbie scene for cold segment users (0-5 points)."""

from typing import Dict, Optional

from app.models import User, UserSegment, FunnelStage
from app.services.llm_service import LLMResponse
from .base_scene import BaseScene, SceneState, SceneResponse


class NewbieScene(BaseScene):
    """Scene for newbie users who need education and gentle guidance."""
    
    def __init__(self, session):
        super().__init__(session)
        self.scene_name = "newbie"
        # Lower confidence threshold for newbies
        self.confidence_threshold = 0.4
    
    async def apply_scene_policy(
        self, 
        user: User, 
        llm_response: LLMResponse, 
        state: SceneState
    ) -> LLMResponse:
        """Apply newbie scene specific policy."""
        
        # Newbies need more education before offers
        if llm_response.next_action == "offer_payment":
            llm_response.next_action = "show_materials"
            
        # Don't offer consultation too early
        if llm_response.next_action == "offer_consult" and state.attempts_count < 3:
            llm_response.next_action = "educate"
            
        # Focus on building trust and understanding
        if llm_response.next_action == "ask" and state.attempts_count > 2:
            llm_response.next_action = "show_materials"
            
        # Add educational tone
        if not any(word in llm_response.reply_text.lower() for word in ["важно", "понимать", "изучить"]):
            llm_response.reply_text = self._add_educational_tone(llm_response.reply_text)
            
        return llm_response
    
    def _add_educational_tone(self, text: str) -> str:
        """Add educational and supportive tone to response."""
        educational_endings = [
            "\n\n💡 *Важно понимать основы, прежде чем делать первые шаги.*",
            "\n\n📚 *Рекомендую сначала изучить базовые принципы.*",
            "\n\n🎯 *Начнём с простого и постепенно углубимся в детали.*"
        ]
        
        # Add one of the educational endings if text doesn't already have educational tone
        if not any(phrase in text for phrase in ["важно", "изучить", "понимать", "основы"]):
            import random
            text += random.choice(educational_endings)
            
        return text
    
    async def determine_next_scene(
        self, 
        user: User, 
        llm_response: LLMResponse, 
        state: SceneState
    ) -> Optional[str]:
        """Determine if should transition to another scene."""
        
        # If user shows readiness for more advanced content
        if (
            llm_response.confidence > 0.8 and 
            llm_response.next_action == "offer_consult" and
            state.attempts_count > 3
        ):
            # Check if user has progressed enough to move to trader scene
            if user.lead_score > 5:
                return "trader"
                
        # If user needs immediate help, escalate
        if llm_response.next_action == "escalate_to_manager":
            return None  # Stay in scene but escalate
            
        # Generally stay in newbie scene for extended education
        return None
    
    def get_scene_prompts(self) -> Dict[str, str]:
        """Get newbie scene specific prompts."""
        return {
            "system_addition": """
СЦЕНАРИЙ: НОВИЧОК В КРИПТОВАЛЮТАХ

Пользователь - новичок в криптовалютах (0-5 баллов). Требует терпеливого обучения.

ТВОЯ РОЛЬ:
- Терпеливый наставник и учитель
- Объясняешь сложные концепции простыми словами
- Не торопишь с решениями
- Даёшь практические советы

ПРИНЦИПЫ РАБОТЫ:
1. Безопасность превыше всего
2. Пошаговое обучение
3. Много примеров и аналогий
4. Мотивация через истории успеха

ЛОГИКА ДЕЙСТВИЙ:
- educate: дай образовательную информацию
- show_materials: покажи базовые материалы
- ask: задай уточняющие вопросы
- build_trust: расскажи о безопасности и надёжности

НЕ предлагай сложные стратегии или быстрые решения.
""",
            "tone": "дружелюбный, терпеливый, образовательный",
            "keywords": ["основы", "безопасность", "пошагово", "изучение", "понимание"]
        }
    
    async def create_educational_response(self, topic: str, user: User) -> SceneResponse:
        """Create educational response for specific topic."""
        
        educational_responses = {
            "basics": {
                "message": """🎓 **Основы криптовалют - твой первый шаг!**

**Что такое криптовалюты простыми словами:**
• Цифровые деньги, которые работают через интернет
• Защищены специальной технологией - блокчейном
• Не контролируются банками или государством

**Почему они популярны:**
✅ Потенциал роста стоимости
✅ Новые возможности для инвестиций
✅ Технология будущего

**С чего начать:**
1. Изучить основы безопасности
2. Попробовать с небольшой суммы
3. Постепенно углублять знания

💡 *Помни: знания - твоя главная защита от ошибок!*""",
                "buttons": [
                    {"text": "📚 Материалы для новичков", "callback_data": "materials:beginners"},
                    {"text": "🛡 Безопасность в криптовалютах", "callback_data": "educate:safety"},
                    {"text": "💬 Задать вопрос", "callback_data": "ask:question"}
                ]
            },
            "safety": {
                "message": """🛡 **Безопасность - это основа всего!**

**Главные правила безопасности:**
1. **Никогда не делись паролями** - даже с близкими
2. **Используй только проверенные платформы** - Binance, Coinbase
3. **Начинай с малого** - не инвестируй все сбережения
4. **Включай двухфакторную аутентификацию** - дополнительная защита

**Как избежать мошенников:**
❌ Не верь обещаниям быстрого обогащения
❌ Не отправляй деньги незнакомцам
❌ Не покупай по советам из случайных чатов

✅ **Изучай, проверяй, думай головой!**

💡 *Лучше потратить время на изучение, чем потерять деньги из-за спешки.*""",
                "buttons": [
                    {"text": "📖 Подробный гайд по безопасности", "callback_data": "materials:security"},
                    {"text": "🎯 Готов изучать дальше", "callback_data": "educate:basics"},
                    {"text": "📞 Консультация по безопасности", "callback_data": "consult:safety"}
                ]
            }
        }
        
        response_data = educational_responses.get(topic, educational_responses["basics"])
        
        return SceneResponse(
            message_text=response_data["message"],
            buttons=response_data["buttons"],
            log_event={
                "scene": self.scene_name,
                "action": "education_provided",
                "topic": topic
            }
        )
    
    async def handle_user_question(self, question: str, user: User) -> SceneResponse:
        """Handle specific user questions with educational approach."""
        
        # Common newbie questions and responses
        if any(word in question.lower() for word in ["сколько", "денег", "вложить"]):
            return SceneResponse(
                message_text="""💰 **Отличный вопрос о стартовом капитале!**

**Универсальный принцип:**
Начинай с суммы, которую не страшно потерять - обычно это 5-10% от твоих сбережений.

**Примеры:**
• Есть 100 000 руб → начни с 5-10 тысяч
• Есть 50 000 руб → начни с 2-5 тысяч
• Есть 20 000 руб → начни с 1-2 тысяч

**Почему так:**
✅ Снижаешь стресс и эмоции
✅ Можешь спокойно учиться
✅ Не рискуешь критически важными деньгами

💡 *Помни: цель первых инвестиций - получить опыт, а не большую прибыль.*""",
                buttons=[
                    {"text": "📊 Пройти тест для точного расчёта", "callback_data": "survey:start"},
                    {"text": "📚 Материалы о планировании бюджета", "callback_data": "materials:budget"},
                    {"text": "📞 Персональная консультация", "callback_data": "consult:offer"}
                ]
            )
        
        # Default educational response
        return SceneResponse(
            message_text="""🤔 **Понимаю твой вопрос!**

Это важная тема, и я хочу дать тебе максимально полезный ответ.

Лучше всего мы сможем разобрать твой вопрос на персональной консультации, где эксперт сможет учесть твою конкретную ситуацию.

А пока предлагаю изучить базовые материалы - они ответят на большинство вопросов новичков! 📚""",
            buttons=[
                {"text": "📚 Материалы для новичков", "callback_data": "materials:beginners"},
                {"text": "📞 Записаться на консультацию", "callback_data": "consult:offer"},
                {"text": "📊 Пройти тест самооценки", "callback_data": "survey:start"}
            ]
        )