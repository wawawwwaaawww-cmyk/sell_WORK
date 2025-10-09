"""Investor scene for hot segment users (10+ points)."""

from typing import Dict, Optional

from app.models import User, UserSegment, FunnelStage
from app.services.llm_service import LLMResponse
from .base_scene import BaseScene, SceneState, SceneResponse


class InvestorScene(BaseScene):
    """Scene for experienced users with high engagement and readiness to invest."""
    
    def __init__(self, session):
        super().__init__(session)
        self.scene_name = "investor"
        # Higher confidence threshold for investors
        self.confidence_threshold = 0.6
    
    async def apply_scene_policy(
        self, 
        user: User, 
        llm_response: LLMResponse, 
        state: SceneState
    ) -> LLMResponse:
        """Apply investor scene specific policy."""
        
        # Investors are ready for offers quickly
        if llm_response.next_action == "educate" and state.attempts_count > 0:
            llm_response.next_action = "offer_consult"
            
        # Push towards payment after consultation offer
        if llm_response.next_action == "offer_consult" and state.attempts_count > 1:
            llm_response.next_action = "offer_payment"
            
        # Focus on exclusive opportunities and high-value propositions
        if llm_response.next_action == "show_materials":
            llm_response.next_action = "show_exclusive"
            
        # Add premium tone
        if not any(word in llm_response.reply_text.lower() for word in ["эксклюзив", "премиум", "vip", "индивидуальн"]):
            llm_response.reply_text = self._add_premium_tone(llm_response.reply_text)
            
        return llm_response
    
    def _add_premium_tone(self, text: str) -> str:
        """Add premium and exclusive tone to response."""
        premium_endings = [
            "\n\n💎 *Для вас доступны эксклюзивные возможности уровня VIP.*",
            "\n\n🏆 *Ваш опыт позволяет рассматривать премиальные стратегии.*",
            "\n\n⭐ *Индивидуальный подход - именно то, что нужно на вашем уровне.*"
        ]
        
        # Add premium tone if not present
        if not any(phrase in text for phrase in ["эксклюзив", "премиум", "vip", "индивидуальн", "уровень"]):
            import random
            text += random.choice(premium_endings)
            
        return text
    
    async def determine_next_scene(
        self, 
        user: User, 
        llm_response: LLMResponse, 
        state: SceneState
    ) -> Optional[str]:
        """Determine if should transition to another scene."""
        
        # Investors generally stay in investor scene unless special cases
        
        # If user shows very high skepticism, move to skeptic scene
        if (
            llm_response.confidence < 0.4 and
            state.attempts_count > 2 and
            user.lead_score > 12
        ):
            return "skeptic"
        
        # If user needs basic education (unlikely but possible)
        if (
            llm_response.next_action == "educate" and 
            state.attempts_count > 3
        ):
            return "trader"
            
        # Stay in investor scene for continued high-value engagement
        return None
    
    def get_scene_prompts(self) -> Dict[str, str]:
        """Get investor scene specific prompts."""
        return {
            "system_addition": """
СЦЕНАРИЙ: ОПЫТНЫЙ ИНВЕСТОР

Пользователь опытен в криптовалютах (10+ баллов). Готов к серьёзным инвестициям и действиям.

ТВОЯ РОЛЬ:
- Консультант уровня VIP
- Предлагаешь эксклюзивные возможности
- Фокусируешься на результатах и ROI
- Подчёркиваешь индивидуальный подход

ПОДХОД К ПОЛЬЗОВАТЕЛЮ:
1. Признавай высокий уровень экспертизы
2. Предлагай премиальные решения
3. Фокусируйся на уникальных возможностях
4. Быстро переходи к предложениям

ЛОГИКА ДЕЙСТВИЙ:
- show_exclusive: покажи эксклюзивные возможности
- offer_premium: предложи премиум консультацию
- offer_payment: предложи оплату программы
- provide_vip_access: дай доступ к VIP материалам

Активно предлагай платные услуги и подчёркивай эксклюзивность.
""",
            "tone": "профессиональный, уверенный, премиальный",
            "keywords": ["эксклюзив", "премиум", "VIP", "индивидуальный", "результат", "ROI"]
        }
    
    async def create_premium_offer(self, offer_type: str, user: User) -> SceneResponse:
        """Create premium offer for investor-level users."""
        
        premium_offers = {
            "consultation": {
                "message": """💎 **Эксклюзивная консультация для опытных инвесторов**

**Ваш уровень экспертизы заслуживает индивидуального подхода.**

🏆 **Что входит в VIP-консультацию:**
• Персональный анализ вашего портфеля
• Эксклюзивные стратегии для крупного капитала  
• Доступ к закрытым инвестиционным возможностям
• Прямая связь с топовыми аналитиками

📊 **Специально для инвесторов вашего уровня:**
✅ Стратегии от $50,000 и выше
✅ Институциональные подходы к криптовалютам  
✅ Хедж-фонды и фонды венчурного капитала
✅ Налоговое планирование и оптимизация

💰 **Результаты наших VIP-клиентов:**
• Средняя доходность: 180-320% в год
• Минимизация рисков через диверсификацию
• Доступ к IPO криптопроектов

**Готовы обсудить ваши инвестиционные цели?** 🤝""",
                "buttons": [
                    {"text": "💎 Записаться на VIP-консультацию", "callback_data": "consult:vip"},
                    {"text": "📊 Анализ портфеля от $100k", "callback_data": "consult:portfolio_vip"},
                    {"text": "🏆 Эксклюзивные стратегии", "callback_data": "materials:exclusive"},
                    {"text": "💰 Узнать стоимость", "callback_data": "payment:vip_consult"}
                ]
            },
            "program": {
                "message": """🚀 **Премиальная программа "Crypto Elite Investor"**

**Программа для серьёзных инвесторов с капиталом от $50,000**

💎 **Эксклюзивные возможности:**
• Закрытый клуб инвесторов (только 50 участников)
• Еженедельные разборы от топ-аналитиков
• Доступ к Pre-Sale и Private Round
• Персональный куратор на 12 месяцев

🏆 **Уникальные инструменты:**
✅ Собственные торговые алгоритмы
✅ Инсайды от партнёров-фондов
✅ Прямые контакты основателей проектов
✅ Эксклюзивные нетворкинг-события

📈 **Результаты участников (2023):**
• 87% показали прибыль выше 200%
• Средний ROI: 340% за год
• 23 участника достигли $1M+ портфель

💰 **Инвестиция в программу:** 299,000 рублей
*Окупается в среднем за 2-3 месяца*

**Количество мест ограничено до 15 декабря.** ⏰""",
                "buttons": [
                    {"text": "💎 Забронировать место", "callback_data": "payment:crypto_elite"},
                    {"text": "📞 Обсудить детали", "callback_data": "consult:program_details"},
                    {"text": "📊 Кейсы участников", "callback_data": "materials:elite_cases"},
                    {"text": "💳 Варианты оплаты", "callback_data": "payment:options"}
                ]
            }
        }
        
        offer_data = premium_offers.get(offer_type, premium_offers["consultation"])
        
        return SceneResponse(
            message_text=offer_data["message"],
            buttons=offer_data["buttons"],
            log_event={
                "scene": self.scene_name,
                "action": "premium_offer_shown",
                "offer_type": offer_type
            }
        )
    
    async def show_exclusive_materials(self, material_type: str, user: User) -> SceneResponse:
        """Show exclusive materials for high-level investors."""
        
        exclusive_materials = {
            "strategies": """🏆 **Эксклюзивные стратегии для крупного капитала**

**Доступно только для инвесторов уровня VIP:**

💎 **Институциональные подходы:**
• Market Making для стейблкоинов (15-25% годовых)
• Арбитраж между регионами (низкий риск, стабильный доход)
• Yield Farming в протоколах уровня Blue Chip

🚀 **Венчурные возможности:**
• Участие в Seed раундах топ-проектов
• Эксклюзивные токен-сейлы (ROI 10-50x)
• Прямые инвестиции в криптостартапы

📊 **Хедж-фонды стратегии:**
• Long/Short позиции с плечом
• Парный трейдинг криптовалют
• Волатильность арбитраж

**Каждая стратегия требует капитала от $50,000**

Хотите получить детальный разбор? 🤝""",
            
            "cases": """💰 **VIP-кейсы: результаты наших топ-клиентов**

**Инвестор #1 - Михаил К. (IT-предприниматель):**
*Начальный капитал: $200,000*
*Результат за 10 месяцев: $840,000 (+320%)*

🎯 **Стратегия:** Комбинация DeFi протоколов + венчурные инвестиции
📈 **Ключевые сделки:** Solana (early), Polygon (seed), Chainlink стейкинг

**Инвестор #2 - Анна Л. (финансовый директор):**
*Начальный капитал: $500,000*
*Результат за 8 месяцев: $1,350,000 (+170%)*

🎯 **Стратегия:** Портфельный подход + арбитраж
📈 **Ключевые позиции:** Bitcoin ETF, Ethereum стейкинг, альткоин-индекс

**Инвестор #3 - Дмитрий Р. (собственник бизнеса):**
*Начальный капитал: $1,000,000*
*Результат за 6 месяцев: $2,100,000 (+110%)*

🎯 **Стратегия:** Институциональный подход
📈 **Фокус:** Market making, yield farming, OTC сделки

**Все они - выпускники программы Crypto Elite Investor** 🏆"""
        }
        
        material_text = exclusive_materials.get(material_type, exclusive_materials["strategies"])
        
        return SceneResponse(
            message_text=material_text,
            buttons=[
                {"text": "💎 Получить полный доступ", "callback_data": "payment:vip_access"},
                {"text": "📞 Консультация с экспертом", "callback_data": "consult:expert_vip"},
                {"text": "📊 Персональная стратегия", "callback_data": "consult:personal_strategy"},
                {"text": "🎯 Начать программу", "callback_data": "payment:crypto_elite"}
            ],
            log_event={
                "scene": self.scene_name,
                "action": "exclusive_material_shown",
                "material_type": material_type
            }
        )