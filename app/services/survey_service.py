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
                "text": "*Какой опыт в инвестициях вы имеете?*\n\nВопрос 1/5",
                "options": {
                    "trading": {"text": "Трейдинг", "points": 2},
                    "crypto_long": {"text": "Долгосрочные инвестиции в криптовалюту", "points": 1},
                    "stocks": {"text": "Фондовый рынок", "points": 1},
                    "deposit": {"text": "Банковский вклад", "points": 0},
                    "no_experience": {"text": "Нет опыта в инвестициях", "points": 0},
                },
            },
            "q2": {
                "text": "*А теперь скажи, какая у тебя основная цель в финансах на ближайший год?*\n\nВопрос 2/5",
                "options": {
                    "passive_income": {"text": "Выйти на пассивный доход", "points": 3},
                    "safety_net": {"text": "Накопить финансовую подушку безопасности", "points": 2},
                    "debt": {"text": "Закрыть долги и кредиты", "points": 2},
                    "start_investing": {"text": "Начать инвестировать", "points": 1},
                    "no_goal": {"text": "Нет финансовой цели", "points": 0},
                },
            },
            "q3": {
                "text": "*Как срочно вы хотите решить свой финансовый вопрос?*\n\nВопрос 3/5",
                "options": {
                    "one_month": {"text": "В течение месяца", "points": 3},
                    "three_months": {"text": "В течение 3-х месяцев", "points": 2},
                    "half_year": {"text": "В течение полугода", "points": 1},
                    "year": {"text": "В течение года", "points": 1},
                    "researching": {"text": "Просто изучаю тему, без конкретных сроков", "points": 0},
                },
            },
            "q4": {
                "text": "С какой суммы готовы начать инвестировать?\n\nВопрос 4/5",
                "options": {
                    "under_10": {"text": "До 10$", "points": 0},
                    "10_100": {"text": "От 10$ до 100$", "points": 1},
                    "100_500": {"text": "От 100$ до 500$", "points": 2},
                    "500_1000": {"text": "От 500$ до 1000$", "points": 2},
                    "over_1000": {"text": "От 1000$", "points": 3},
                },
            },
            "q5": {
                "text": "*А как думаешь, если будет видно, что обучение ускоряет прогресс — готов ли ты в него инвестировать?*\n\nВопрос 5/5",
                "options": {
                    "yes": {"text": "Да", "points": 2},
                    "no": {"text": "Нет", "points": 0},
                },
            },
        }

        # Response confirmations
        self.confirmations = {
            "q1": {
                "trading": (
                    "Круто 🔥 У тебя уже есть опыт в активной торговле. Значит, можно смотреть на инструменты с динамикой и возможностью быстрых решений — трейдинг на споте или фьючерсы."
                ),
                "crypto_long": (
                    "Отличный выбор 🙌 Долгосрок — это стратегия, которая помогает не реагировать на каждое колебание рынка, а просто держать сильные активы. Тут хорошо работает стейкинг."
                ),
                "stocks": (
                    "Здорово 👌 Значит, у тебя уже есть опыт с традиционными активами. В крипте тоже есть аналоги фондового рынка — крипто-ETF или диверсифицированные портфели."
                ),
                "deposit": (
                    "Понял 🙂 Ты выбирал надёжность. В крипторынке тоже есть похожие инструменты — стейблкоины и стейкинг с фиксированной доходностью."
                ),
                "no_experience": (
                    "Отлично, значит, можно начать с базы 💡 В крипторынке есть простые и понятные шаги для новичков: небольшие покупки и стейблкоины."
                ),
            },
            "q2": {
                "passive_income": (
                    "Отличная цель 💡 Пассивный доход в крипторынке можно строить через стейкинг или DeFi-инструменты."
                ),
                "safety_net": (
                    "Здорово 👍 Подушка безопасности — это надёжная база. В крипторынке для этого часто используют стейблкоины и простые стратегии накопления."
                ),
                "debt": (
                    "Понимаю тебя 🙌 В такой ситуации важно минимизировать риски и использовать максимально надёжные инструменты."
                ),
                "start_investing": (
                    "Отлично 👌 Начало всегда самое важное. Можно зайти с простых стратегий и постепенно увеличивать портфель."
                ),
                "no_goal": (
                    "Хорошо 🙂 Иногда полезно сначала изучить рынок, а цель сформируется по ходу."
                ),
            },
            "q3": {
                "one_month": (
                    "Понял 🔥 Значит, нужен быстрый результат. В крипторынке это могут быть активные инструменты — трейдинг или краткосрочные стратегии в DeFi."
                ),
                "three_months": (
                    "Отлично 👍 За 3 месяца можно собрать рабочую стратегию: часть в стейкинг, часть в топовые монеты, плюс простые DeFi-инструменты."
                ),
                "half_year": (
                    "Хорошо 🙌 Полгода — хороший срок для постепенной стратегии: накопления в стейблкоинах и покупки криптовалюты."
                ),
                "year": (
                    "Понял 🙂 Год позволяет спокойно выстроить долгосрочную стратегию — например, портфель из BTC, ETH и стейблкоинов со стейкингом."
                ),
                "researching": (
                    "Отлично 👌 Значит, пока в приоритете обучение и пробные шаги. Тут можно начинать с маленьких сумм и безопасных инструментов."
                ),
            },
            "q4": {
                "under_10": (
                    "Круто, даже с небольшой суммы можно начинать пробовать 💡 Это позволит освоиться с кошельками, переводами и первыми сделками без риска."
                ),
                "10_100": (
                    "Хорошо 🙌 С такой суммы удобно стартовать через простые стратегии: DCA на BTC/ETH или небольшой стейкинг на бирже. Это идеальный вариант для практики."
                ),
                "100_500": (
                    "Отличная сумма для начала 👍 Уже можно собрать мини-портфель: часть в стейблкоинах, часть в топовых криптомонетах, плюс попробовать DeFi."
                ),
                "500_1000": (
                    "Супер 🚀 Такой бюджет открывает возможность полноценной диверсификации: стейблы, BTC/ETH, топовые альткоины и даже доходные протоколы."
                ),
                "over_1000": (
                    "Отличный старт 🔥 На такой сумме можно собрать уже серьёзный портфель: BTC, ETH, альткоины, стейкинг, а при желании даже NFT или GameFi."
                ),
            },
            "q5": {
                "yes": (
                    "Супер! 🙌 Это самый быстрый способ избежать ошибок и сразу применить проверенные стратегии.\n"
                    "Я могу познакомить тебя с нашим экспертом — он разберёт твои цели и подскажет оптимальную программу обучения. "
                    "Это займёт всего 15–20 минут, и после разговора у тебя уже будет чёткий план действий.\n"
                    "📌 Хочешь, я запишу тебя на удобное время?\n"
                    "👉 Альтернативно (если закрываешь продажу в чате): Тогда могу прямо сейчас рассказать, какая программа подходит "
                    "именно под твои ответы. Она даст {program_result}.\n"
                    "Хочешь, я скину детали и варианты участия?"
                ),
                "no": (
                    "Понимаю тебя 👍 Многие сначала пробуют самостоятельно. Но чтобы ускорить процесс и избежать ошибок, у нас есть "
                    "куратор, который даёт пошаговый план и отвечает на вопросы.\n"
                    "Могу предложить просто бесплатную консультацию, где он поможет тебе структурировать твой путь в крипте. Без "
                    "обязательств.\n"
                    "📌 Хочешь, я запишу тебя?\n"
                    "👉 Альтернативно (в переписке): Хорошо 🙂 Тогда я могу рассказать про базовую программу — она как раз рассчитана "
                    "для самостоятельного старта. Минимум теории, максимум практики. Хочешь, скину описание?"
                ),
            },
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

    def determine_program_recommendation(
        self,
        answers_map: Dict[str, str],
        total_score: int
    ) -> Dict[str, str]:
        """Determine recommended program based on survey answers."""
        program_catalog = {
            "community": {
                "code": "community",
                "name": "Криптосообщество",
                "key_result": "стабильный пассивный доход на проверенных криптоактивах",
                "description": (
                    "💼 <b>Криптосообщество</b> — закрытое комьюнити с еженедельными разборами портфелей, "
                    "сигналами по стейкингу и поддержкой кураторов.\n\n"
                    "📌 Подходит, если хочешь системно наращивать капитал без суеты: получишь стратегию накопления, "
                    "разбор рисков и готовые инструкции по пассивным инструментам."
                ),
            },
            "profit": {
                "code": "profit",
                "name": "Доход за час",
                "key_result": "безопасный старт в крипте с пошаговыми инструкциями",
                "description": (
                    "🚀 <b>Доход за час</b> — интенсив для быстрого старта. За 60 минут собираешь личный план действий: "
                    "какие биржи открыть, как сделать первые сделки и как не слить депозит.\n\n"
                    "📌 Идеально для новичков: много практики, шаблоны портфелей, поддержка куратора на запуске."
                ),
            },
            "fast_money": {
                "code": "fast_money",
                "name": "Быстрые деньги",
                "key_result": "ускоренный рост капитала через активные стратегии",
                "description": (
                    "⚡ <b>Быстрые деньги</b> — практический курс по краткосрочным стратегиям: разбор DeFi-инструментов, "
                    "арбитражных возможностей и быстрой торговли.\n\n"
                    "📌 Для тех, кому важно увидеть результат в ближайшие месяцы и кто готов действовать по чётким чек-листам."
                ),
            },
            "vip": {
                "code": "vip",
                "name": "Big Money VIP",
                "key_result": "персональную стратегию по трейдингу с доходностью выше рынка",
                "description": (
                    "💎 <b>Big Money VIP</b> — индивидуальная работа с экспертом по трейдингу: авторские стратегии, "
                    "разбор сделок и контроль рисков.\n\n"
                    "📌 Для опытных участников с капиталом: получишь профессиональный разбор и поддержку в сделках."
                ),
            },
        }

        experience = answers_map.get("q1")
        goal = answers_map.get("q2")
        urgency = answers_map.get("q3")
        budget = answers_map.get("q4")

        if experience == "trading" or total_score >= 11:
            recommendation = program_catalog["vip"]
        elif urgency in {"one_month", "three_months"} and budget in {"100_500", "500_1000", "over_1000"}:
            recommendation = program_catalog["fast_money"]
        elif goal in {"passive_income", "safety_net"}:
            recommendation = program_catalog["community"]
        elif experience in {"no_experience", "deposit"} or budget in {"under_10", "10_100"}:
            recommendation = program_catalog["profit"]
        else:
            recommendation = program_catalog["community"]

        self.logger.info(
            "Program recommendation prepared",
            answers=answers_map,
            total_score=total_score,
            program=recommendation["code"],
        )

        return recommendation

    async def generate_summary(self, user_id: int) -> Dict[str, Any]:
        """Generate user survey summary."""
        answers = await self.repository.get_user_answers(user_id)
        total_score = sum(answer.points for answer in answers)

        answers_map = {answer.question_code: answer.answer_code for answer in answers}

        # Determine segment
        if total_score <= 4:
            segment = "cold"
            segment_desc = "Новичок в криптовалютах"
        elif total_score <= 9:
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

        program_recommendation = self.determine_program_recommendation(answers_map, total_score)

        return {
            "total_score": total_score,
            "segment": segment,
            "segment_description": segment_desc,
            "profile_summary": " | ".join(profile_parts),
            "answers_count": len(answers),
            "answers": answers_map,
            "program": program_recommendation,
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