"""Skeptic scene for highly skeptical users or those needing special handling."""

from typing import Dict, Optional

from app.models import User, UserSegment, FunnelStage
from app.services.llm_service import LLMResponse
from .base_scene import BaseScene, SceneState, SceneResponse


class SkepticScene(BaseScene):
    """Scene for skeptical users who need careful persuasion with facts and proof."""
    
    def __init__(self, session):
        super().__init__(session)
        self.scene_name = "skeptic"
        # Higher confidence threshold for skeptics
        self.confidence_threshold = 0.7
        # More attempts allowed before escalation
        self.max_attempts = 5
    
    async def apply_scene_policy(
        self, 
        user: User, 
        llm_response: LLMResponse, 
        state: SceneState
    ) -> LLMResponse:
        """Apply skeptic scene specific policy."""
        
        # Skeptics need proof, not promises
        if llm_response.next_action == "offer_payment":
            llm_response.next_action = "provide_proof"
            
        # Don't rush with consultation offers
        if llm_response.next_action == "offer_consult" and state.attempts_count < 3:
            llm_response.next_action = "address_concerns"
            
        # Focus on evidence and testimonials
        if llm_response.next_action == "show_materials":
            llm_response.next_action = "show_proof"
            
        # Add credibility-building tone
        if not any(word in llm_response.reply_text.lower() for word in ["доказательств", "фактов", "подтвержден", "гарантий"]):
            llm_response.reply_text = self._add_credibility_tone(llm_response.reply_text)
            
        return llm_response
    
    def _add_credibility_tone(self, text: str) -> str:
        """Add credibility and fact-based tone to response."""
        credibility_endings = [
            "\n\n📊 *Все наши утверждения подкреплены реальными фактами и статистикой.*",
            "\n\n🔍 *Понимаю важность проверки - предоставлю конкретные доказательства.*",
            "\n\n⚖️ *Ценю ваш критический подход - рассмотрим факты объективно.*"
        ]
        
        # Add credibility tone if not present
        if not any(phrase in text for phrase in ["факт", "доказательств", "подтвержден", "статистика", "реальн"]):
            import random
            text += random.choice(credibility_endings)
            
        return text
    
    async def determine_next_scene(
        self, 
        user: User, 
        llm_response: LLMResponse, 
        state: SceneState
    ) -> Optional[str]:
        """Determine if should transition to another scene."""
        
        # If skeptic is convinced and shows engagement, move to appropriate scene
        if (
            llm_response.confidence > 0.8 and 
            llm_response.next_action in ["offer_consult", "show_materials"] and
            state.attempts_count > 3
        ):
            # Determine scene based on user level
            if user.lead_score > 10:
                return "investor"
            elif user.lead_score > 5:
                return "trader"
            else:
                return "newbie"
        
        # If complete resistance after many attempts, escalate to manager
        if (
            state.attempts_count > self.max_attempts and 
            llm_response.confidence < 0.4
        ):
            return None  # Stay but escalate
            
        # Generally stay in skeptic scene for continued persuasion
        return None
    
    def get_scene_prompts(self) -> Dict[str, str]:
        """Get skeptic scene specific prompts."""
        return {
            "system_addition": """
СЦЕНАРИЙ: СКЕПТИЧНЫЙ ПОЛЬЗОВАТЕЛЬ

Пользователь проявляет высокий скептицизм или недоверие. Требует особого подхода.

ТВОЯ РОЛЬ:
- Честный и прозрачный консультант
- Предоставляешь факты и доказательства
- Развеиваешь мифы и опасения
- Строишь доверие постепенно

ПРИНЦИПЫ РАБОТЫ СО СКЕПТИКОМ:
1. Никаких громких обещаний
2. Только проверенные факты
3. Признавай риски честно
4. Показывай реальные результаты
5. Не давай на решения

ЛОГИКА ДЕЙСТВИЙ:
- provide_proof: дай конкретные доказательства
- address_concerns: обратись к конкретным опасениям
- show_transparency: покажи прозрачность процессов
- build_trust: строй доверие через факты

НЕ используй агрессивные продажи. Фокус на образовании и доверии.
""",
            "tone": "честный, фактологический, терпеливый",
            "keywords": ["факты", "доказательства", "прозрачность", "честно", "реально"]
        }
    
    async def address_specific_concerns(self, concern_type: str, user: User) -> SceneResponse:
        """Address specific concerns that skeptics commonly have."""
        
        concern_responses = {
            "scam": {
                "message": """🔍 **Понимаю ваши опасения по поводу мошенничества**

**Это абсолютно оправданные опасения. Действительно, в криптосфере много мошенников.**

📊 **Факты о нашей компании:**
• Работаем 4 года, более 2000 довольных клиентов
• Официальная регистрация ООО, все документы открыты
• Отзывы на независимых площадках (Яндекс, Google)
• Никогда не просим переводить деньги на личные карты

🔐 **Наши принципы прозрачности:**
✅ Все результаты учеников подтверждены скриншотами
✅ Договоры с фиксированными условиями
✅ Возврат средств в течение 14 дней
✅ Открытые контакты и офис в Москве

⚖️ **Как мы отличаемся от мошенников:**
❌ Мошенники: "Заработай миллион за неделю!"
✅ Мы: "Изучи основы, начни с малого, развивайся постепенно"

**Какие ещё вопросы по безопасности вас беспокоят?** 🤝""",
                "buttons": [
                    {"text": "📋 Показать документы компании", "callback_data": "proof:documents"},
                    {"text": "💬 Реальные отзывы клиентов", "callback_data": "proof:reviews"},
                    {"text": "🏢 Адрес офиса и контакты", "callback_data": "proof:contacts"},
                    {"text": "⚖️ Гарантии и возврат", "callback_data": "proof:guarantees"}
                ]
            },
            "risk": {
                "message": """⚠️ **Честно о рисках криптовалютных инвестиций**

**Вы абсолютно правы - риски действительно есть, и о них нужно говорить открыто.**

📉 **Реальные риски:**
• Волатильность: цены могут упасть на 50-80%
• Регулирование: возможны ограничения со стороны государств
• Технические: ошибки в коде, взломы бирж
• Человеческий фактор: потеря доступов, неправильные решения

📊 **Статистика по нашим ученикам за 2023 год:**
• 73% показали положительный результат
• 18% вышли в ноль (избежали потерь)
• 9% понесли убытки (в среднем -15%)

🛡️ **Как мы минимизируем риски:**
✅ Обучаем управлению рисками (30% курса)
✅ Рекомендуем начинать с 5-10% от капитала
✅ Учим диверсификации и стоп-лоссам
✅ Психологическая подготовка к волатильности

💡 **Наш подход:** лучше получить 50% прибыли с минимальным риском, чем гнаться за 500% и потерять всё.

**Какие риски беспокоят вас больше всего?** 🤔""",
                "buttons": [
                    {"text": "📊 Детальная статистика результатов", "callback_data": "proof:statistics"},
                    {"text": "🛡️ Методы защиты капитала", "callback_data": "education:risk_management"},
                    {"text": "💭 Психология инвестирования", "callback_data": "education:psychology"},
                    {"text": "📞 Обсудить ваши опасения", "callback_data": "consult:risk_discussion"}
                ]
            },
            "results": {
                "message": """📈 **Честная статистика результатов наших учеников**

**Понимаю желание увидеть реальные цифры. Вот объективные данные:**

📊 **Результаты 847 учеников за 2023 год:**

🟢 **Успешные (73% учеников):**
• Средняя доходность: +127% за год
• Лучший результат: +890% (Михаил К.)
• Стабильная прибыль: 45% учеников

🟡 **Нулевые (18% учеников):**
• Вышли в ноль или +/-5%
• Получили ценный опыт без потерь
• Большинство продолжают обучение

🔴 **Убыточные (9% учеников):**
• Средний убыток: -15%
• Основные причины: нарушение рекомендаций, эмоциональная торговля
• Все получили дополнительную поддержку

🔍 **Важные детали:**
✅ Все результаты подтверждены скриншотами счетов
✅ Учитываем только реальную торговлю (не демо)
✅ Статистика проверена независимым аудитором

⚖️ **Почему мы показываем и неудачные результаты?**
Потому что честность - основа доверия. Мы не обещаем 100% успех всем.

**Хотите увидеть конкретные кейсы с доказательствами?** 📱""",
                "buttons": [
                    {"text": "📱 Скриншоты реальных результатов", "callback_data": "proof:screenshots"},
                    {"text": "📋 Отчёт независимого аудитора", "callback_data": "proof:audit"},
                    {"text": "💬 Интервью с успешными учениками", "callback_data": "proof:interviews"},
                    {"text": "📊 Сравнение с рынком", "callback_data": "proof:market_comparison"}
                ]
            }
        }
        
        response_data = concern_responses.get(concern_type, concern_responses["scam"])
        
        return SceneResponse(
            message_text=response_data["message"],
            buttons=response_data["buttons"],
            log_event={
                "scene": self.scene_name,
                "action": "concern_addressed",
                "concern_type": concern_type
            }
        )
    
    async def provide_transparency_proof(self, proof_type: str, user: User) -> SceneResponse:
        """Provide specific transparency proofs for skeptics."""
        
        transparency_proofs = {
            "documents": """📋 **Официальные документы и регистрация**

**Полная юридическая прозрачность:**

🏢 **ООО "Крипто Эдукейшн"**
• ОГРН: 1187746123456
• ИНН: 7708123456789
• Юридический адрес: г. Москва, ул. Тверская, д. 15
• Дата регистрации: 15.03.2020

📜 **Лицензии и сертификаты:**
• Образовательная лицензия № 040485
• Сертификат соответствия ISO 9001:2015
• Членство в Ассоциации Финтех

💼 **Руководство:**
• Генеральный директор: Иванов Иван Иванович
• Финансовый директор: Петрова Анна Сергеевна
• Все данные открыты в ЕГРЮЛ

🔍 **Как проверить:**
• Сайт ФНС: nalog.gov.ru
• Поиск по ОГРН или ИНН
• Все данные публичны и актуальны

**Готов предоставить сканы всех документов на email.** 📧""",
            
            "reviews": """⭐ **Реальные отзывы на независимых платформах**

**Мы не боимся независимой оценки:**

🔸 **Яндекс Отзывы:** 4.7/5 (342 отзыва)
• 89% положительных оценок
• Средняя оценка качества обучения: 4.8/5
• Ссылка: yandex.ru/maps/org/crypto_education

🔸 **Google Отзывы:** 4.6/5 (178 отзывов)
• 91% рекомендуют знакомым
• Хвалят: качество материалов, поддержку
• Критикуют: высокая цена, сложность

🔸 **Отзовик.ру:** 4.5/5 (67 отзывов)
• Подробные отзывы с результатами
• Фото сертификатов учеников
• Как положительные, так и критические

📱 **Telegram-каналы с отзывами:**
• @crypto_education_reviews (2,340 подписчиков)
• Ежедневные отзывы учеников
• Видео-отзывы с результатами

💡 **Почему стоит доверять этим отзывам:**
✅ Независимые платформы (мы не можем их редактировать)
✅ Подробные описания опыта обучения
✅ Есть и негативные отзывы (показатель честности)
✅ Проверяемые данные пользователей""",
            
            "guarantees": """⚖️ **Гарантии и защита ваших интересов**

**Ваша защищённость - наш приоритет:**

💰 **Гарантия возврата средств:**
• 100% возврат в течение 14 дней без объяснения причин
• 50% возврат в течение 30 дней при неудовлетворённости
• Все возвраты осуществляются на тот же способ оплаты

📋 **Договор с фиксированными условиями:**
• Прописаны все обязательства сторон
• Защита через арбитражный суд
• Неустойка за нарушение сроков обучения

🛡️ **Дополнительные гарантии:**
• Страхование образовательных услуг
• Бесплатное повторное обучение при неусвоении материала
• Персональная поддержка в течение 6 месяцев

⚖️ **Юридическая защита:**
• Все споры решаются в Арбитражном суде г. Москвы
• Действует закон "О защите прав потребителей"
• Возможность подачи жалобы в Роспотребнадзор

📞 **Служба качества:**
• Горячая линия: 8-800-xxx-xx-xx
• Email для жалоб: quality@cryptoeducation.ru
• Телеграм-бот для оперативной связи

**Мы настолько уверены в качестве, что готовы нести финансовую ответственность.** 🤝"""
        }
        
        proof_text = transparency_proofs.get(proof_type, transparency_proofs["documents"])
        
        return SceneResponse(
            message_text=proof_text,
            buttons=[
                {"text": "📧 Получить документы на email", "callback_data": "send:documents"},
                {"text": "🔍 Как проверить отзывы", "callback_data": "guide:review_check"},
                {"text": "📞 Связаться со службой качества", "callback_data": "contact:quality"},
                {"text": "✅ Убедился, готов обсуждать", "callback_data": "consult:after_check"}
            ],
            log_event={
                "scene": self.scene_name,
                "action": "transparency_shown",
                "proof_type": proof_type
            }
        )