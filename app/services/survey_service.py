"""Survey repository for managing survey data."""

from typing import List, Optional, Dict, Any

import structlog
from sqlalchemy import select, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SurveyAnswer


class SurveyRepository:
    """Repository for survey database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def save_answer(
        self,
        user_id: int,
        question_code: str,
        answer_code: str,
        points: int
    ) -> SurveyAnswer:
        """Save survey answer."""
        answer = SurveyAnswer(
            user_id=user_id,
            question_code=question_code,
            answer_code=answer_code,
            points=points
        )
        
        self.session.add(answer)
        await self.session.flush()
        await self.session.refresh(answer)
        
        self.logger.info(
            "Survey answer saved",
            user_id=user_id,
            question=question_code,
            answer=answer_code,
            points=points
        )
        
        return answer
    
    async def get_user_answers(self, user_id: int) -> List[SurveyAnswer]:
        """Get all survey answers for a user."""
        stmt = select(SurveyAnswer).where(
            SurveyAnswer.user_id == user_id
        ).order_by(SurveyAnswer.created_at)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_answer_by_question(
        self,
        user_id: int,
        question_code: str
    ) -> Optional[SurveyAnswer]:
        """Get specific answer by question code."""
        stmt = select(SurveyAnswer).where(
            and_(
                SurveyAnswer.user_id == user_id,
                SurveyAnswer.question_code == question_code
            )
        )
        
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def calculate_total_score(self, user_id: int) -> int:
        """Calculate total survey score for user."""
        answers = await self.get_user_answers(user_id)
        return sum(answer.points for answer in answers)
    
    async def delete_user_answers(self, user_id: int):
        """Delete all survey answers for a user."""
        stmt = delete(SurveyAnswer).where(SurveyAnswer.user_id == user_id)
        await self.session.execute(stmt)
        self.logger.info("Deleted previous survey answers", user_id=user_id)
    
    async def is_survey_complete(self, user_id: int) -> bool:
        """Check if user has completed all 5 questions."""
        answers = await self.get_user_answers(user_id)
        question_codes = {answer.question_code for answer in answers}
        required_questions = {"q1", "q2", "q3", "q4", "q5"}
        return required_questions.issubset(question_codes)


class SurveyService:
    """Service for survey business logic."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = SurveyRepository(session)
        self.logger = structlog.get_logger()
        
        # Define survey questions and scoring
        self.questions = {
            "q1": {
                "text": "*Какой опыт в инвестициях вы имеете?*",
                "options": {
                    "trading": {"text": "Трейдинг", "points": 2},
                    "crypto_long": {"text": "Долгосрочные инвестиции в криптовалюту", "points": 1},
                    "stock_market": {"text": "Фондовый рынок", "points": 1},
                    "bank_deposit": {"text": "Банковский вклад", "points": 0},
                    "no_experience": {"text": "Нет опыта в инвестициях", "points": 0}
                }
            },
            "q2": {
                "text": "*А теперь скажи, какая у тебя основная цель в финансах на ближайший год?*",
                "options": {
                    "passive_income": {"text": "Выйти на пассивный доход", "points": 3},
                    "safety_net": {"text": "Накопить финансовую подушку безопасности", "points": 2},
                    "close_debts": {"text": "Закрыть долги и кредиты", "points": 2},
                    "start_investing": {"text": "Начать инвестировать", "points": 1},
                    "no_goal": {"text": "Нет финансовой цели", "points": 0}
                }
            },
            "q3": {
                "text": "*Как срочно вы хотите решить свой финансовый вопрос?*",
                "options": {
                    "one_month": {"text": "В течение месяца", "points": 3},
                    "three_months": {"text": "В течение 3-х месяцев", "points": 2},
                    "half_year": {"text": "В течение полугода", "points": 1},
                    "one_year": {"text": "В течение года", "points": 1},
                    "just_learning": {"text": "Просто изучаю тему, без конкретных сроков", "points": 0}
                }
            },
            "q4": {
                "text": "*С какой суммы готовы начать инвестировать?*",
                "options": {
                    "under_10": {"text": "До 10$", "points": 0},
                    "ten_to_hundred": {"text": "От 10$ до 100$", "points": 1},
                    "hundred_to_five_hundred": {"text": "От 100$ до 500$", "points": 2},
                    "five_hundred_to_thousand": {"text": "От 500$ до 1000$", "points": 2},
                    "over_thousand": {"text": "От 1000$", "points": 3}
                }
            },
            "q5": {
                "text": "*А как думаешь, если будет видно, что обучение ускоряет прогресс — готов ли ты в него инвестировать?*",
                "options": {
                    "ready_to_learn": {"text": "Да", "points": 2},
                    "not_ready": {"text": "Нет", "points": 0}
                }
            }
        }

        # Response confirmations
        self.confirmations = {
            "q1": {
                "trading": (
                    "Круто 🔥 У тебя уже есть опыт в активной торговле. Значит, можно смотреть на инструменты с динамикой и"
                    " возможностью быстрых решений — трейдинг на споте или фьючерсы.\n\n"
                ),
                "crypto_long": (
                    "Отличный выбор 🙌 Долгосрок — это стратегия, которая помогает не реагировать на каждое колебание рынка, а"
                    " просто держать сильные активы. Тут хорошо работает стейкинг.\n\n"
                ),
                "stock_market": (
                    "Здорово 👌 Значит, у тебя уже есть опыт с традиционными активами. В крипте тоже есть аналоги фондового рынка"
                    " — крипто-ETF или диверсифицированные портфели.\n\n"
                ),
                "bank_deposit": (
                    "Понял 🙂 Ты выбирал надёжность. В крипторынке тоже есть похожие инструменты — стейблкоины и стейкинг с"
                    " фиксированной доходностью.\n\n"
                ),
                "no_experience": (
                    "Отлично, значит, можно начать с базы 💡 В крипторынке есть простые и понятные шаги для новичков: небольшие"
                    " покупки и стейблкоины.\n\n"
                )
            },
            "q2": {
                "passive_income": (
                    "Отличная цель 💡 Пассивный доход в крипторынке можно строить через стейкинг или DeFi-инструменты.\n\n"
                ),
                "safety_net": (
                    "Здорово 👍 Подушка безопасности — это надёжная база. В крипторынке для этого часто используют стейблкоины и"
                    " простые стратегии накопления.\n\n"
                ),
                "close_debts": (
                    "Понимаю тебя 🙌 В такой ситуации важно минимизировать риски и использовать максимально надёжные инструменты.\n\n"
                ),
                "start_investing": (
                    "Отлично 👌 Начало всегда самое важное. Можно зайти с простых стратегий и постепенно увеличивать портфель.\n\n"
                ),
                "no_goal": (
                    "Хорошо 🙂 Иногда полезно сначала изучить рынок, а цель сформируется по ходу.\n\n"
                )
            },
            "q3": {
                "one_month": (
                    "Понял 🔥 Значит, нужен быстрый результат. В крипторынке это могут быть активные инструменты — трейдинг или"
                    " краткосрочные стратегии в DeFi.\n\n"
                ),
                "three_months": (
                    "Отлично 👍 За 3 месяца можно собрать рабочую стратегию: часть в стейкинг, часть в топовые монеты, плюс простые"
                    " DeFi-инструменты.\n\n"
                ),
                "half_year": (
                    "Хорошо 🙌 Полгода — хороший срок для постепенной стратегии: накопления в стейблкоинах и покупки криптовалюты.\n\n"
                ),
                "one_year": (
                    "Понял 🙂 Год позволяет спокойно выстроить долгосрочную стратегию — например, портфель из BTC, ETH и"
                    " стейблкоинов со стейкингом.\n\n"
                ),
                "just_learning": (
                    "Отлично 👌 Значит, пока в приоритете обучение и пробные шаги. Тут можно начинать с маленьких сумм и безопасных"
                    " инструментов.\n\n"
                )
            },
            "q4": {
                "under_10": (
                    "Круто, даже с небольшой суммы можно начинать пробовать 💡 Это позволит освоиться с кошельками, переводами и"
                    " первыми сделками без риска.\n\n"
                ),
                "ten_to_hundred": (
                    "Хорошо 🙌 С такой суммы удобно стартовать через простые стратегии: DCA на BTC/ETH или небольшой стейкинг на"
                    " бирже. Это идеальный вариант для практики.\n\n"
                ),
                "hundred_to_five_hundred": (
                    "Отличная сумма для начала 👍 Уже можно собрать мини-портфель: часть в стейблкоинах, часть в топовых криптомонетах,"
                    " плюс попробовать DeFi.\n\n"
                ),
                "five_hundred_to_thousand": (
                    "Супер 🚀 Такой бюджет открывает возможность полноценной диверсификации: стейблы, BTC/ETH, топовые альткоины и"
                    " даже доходные протоколы.\n\n"
                ),
                "over_thousand": (
                    "Отличный старт 🔥 На такой сумме можно собрать уже серьёзный портфель: BTC, ETH, альткоины, стейкинг, а при"
                    " желании даже NFT или GameFi.\n\n"
                )
            },
            "q5": {
                "ready_to_learn": (
                    "Супер! 🙌 Это самый быстрый способ избежать ошибок и сразу применить проверенные стратегии.\n"
                    "Я могу познакомить тебя с нашим экспертом — он разберёт твои цели и подскажет оптимальную программу обучения."
                    " Это займёт всего 15–20 минут, и после разговора у тебя уже будет чёткий план действий.\n"
                    "📌 Хочешь, я запишу тебя на удобное время?"
                ),
                "not_ready": "Понимаю 🙂 Тогда давай подведём итоги и посмотрим, какие шаги будут комфортны именно тебе."
            }
        }
    
    async def clear_user_answers(self, user_id: int):
        """Clear all previous survey answers for a user."""
        await self.repository.delete_user_answers(user_id)

    async def get_question(self, question_code: str) -> Optional[Dict[str, Any]]:
        """Get question data by code."""
        return self.questions.get(question_code)
    
    async def save_answer(
        self,
        user_id: int,
        question_code: str,
        answer_code: str
    ) -> SurveyAnswer:
        """Save survey answer with points calculation."""
        question = self.questions.get(question_code)
        if not question:
            raise ValueError(f"Invalid question code: {question_code}")
        
        option = question["options"].get(answer_code)
        if not option:
            raise ValueError(f"Invalid answer code: {answer_code}")
        
        return await self.repository.save_answer(
            user_id=user_id,
            question_code=question_code,
            answer_code=answer_code,
            points=option["points"]
        )
    
    async def get_confirmation_text(
        self,
        question_code: str,
        answer_code: str
    ) -> str:
        """Get confirmation text for answer."""
        confirmations = self.confirmations.get(question_code, {})
        return confirmations.get(answer_code, "Отлично!")
    
    async def get_next_question_code(self, user_id: int) -> Optional[str]:
        """Get next unanswered question code."""
        answers = await self.repository.get_user_answers(user_id)
        answered_questions = {answer.question_code for answer in answers}
        
        for q_code in ["q1", "q2", "q3", "q4", "q5"]:
            if q_code not in answered_questions:
                return q_code
        
        return None  # All questions answered
    
    async def generate_summary(self, user_id: int) -> Dict[str, Any]:
        """Generate user survey summary."""
        answers = await self.repository.get_user_answers(user_id)
        total_score = sum(answer.points for answer in answers)
        
        # Determine segment
        if total_score <= 5:
            segment = "cold"
            segment_desc = "Новичок в криптовалютах"
        elif total_score <= 10:
            segment = "warm"
            segment_desc = "Имеет базовые знания"
        else:
            segment = "hot"
            segment_desc = "Продвинутый инвестор"
        
        # Build profile text
        profile_parts = []
        for answer in answers:
            question = self.questions.get(answer.question_code)
            if question:
                option = question["options"].get(answer.answer_code)
                if option:
                    profile_parts.append(option["text"])
        
        return {
            "total_score": total_score,
            "segment": segment,
            "segment_description": segment_desc,
            "profile_summary": " | ".join(profile_parts),
            "answers_count": len(answers)
        }
    
    async def get_survey_summary(self, user_id: int) -> Optional[str]:
        """Get formatted survey summary for LLM context."""
        try:
            if not await self.repository.is_survey_complete(user_id):
                return None
            
            summary = await self.generate_summary(user_id)
            return f"""Анкета пройдена: {summary['segment_description']} ({summary['total_score']}/13 баллов)
Профиль: {summary['profile_summary']}"""
            
        except Exception as e:
            self.logger.error("Error getting survey summary", error=str(e))
            return None