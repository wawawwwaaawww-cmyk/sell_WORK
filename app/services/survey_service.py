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
                "text": "🎯 **Какой у тебя опыт работы с криптовалютами?**",
                "options": {
                    "beginner": {"text": "👶 Совсем новичок", "points": 1},
                    "some_exp": {"text": "📈 Немного торговал/инвестировал", "points": 2},
                    "advanced": {"text": "💼 Есть опыт, хочу развиваться", "points": 3}
                }
            },
            "q2": {
                "text": "💰 **Какая у тебя основная цель?**",
                "options": {
                    "learn": {"text": "📚 Изучить основы", "points": 1},
                    "income": {"text": "💵 Получать стабильный доход", "points": 2},
                    "returns": {"text": "🚀 Максимизировать доходность", "points": 3}
                }
            },
            "q3": {
                "text": "⚖️ **Как ты относишься к рискам?**",
                "options": {
                    "conservative": {"text": "🛡️ Предпочитаю безопасность", "points": 1},
                    "moderate": {"text": "⚖️ Умеренные риски ради роста", "points": 2},
                    "aggressive": {"text": "🎯 Готов рисковать ради высокой прибыли", "points": 3}
                }
            },
            "q4": {
                "text": "⏰ **Сколько времени готов уделять?**",
                "options": {
                    "casual": {"text": "⏰ 1-2 часа в неделю", "points": 1},
                    "parttime": {"text": "📅 Несколько часов в день", "points": 2},
                    "fulltime": {"text": "🕐 Готов заниматься активно", "points": 3}
                }
            },
            "q5": {
                "text": "💼 **Какой стартовый капитал планируешь?**",
                "options": {
                    "small": {"text": "💰 До 100,000 рублей", "points": 1},
                    "medium": {"text": "💎 100,000 - 500,000 рублей", "points": 2},
                    "large": {"text": "💸 Свыше 500,000 рублей", "points": 3}
                }
            }
        }
        
        # Response confirmations
        self.confirmations = {
            "q1": {
                "beginner": "Отлично! Все когда-то начинали с нуля 👍",
                "some_exp": "Здорово! Опыт — это хорошая база для развития 📈",
                "advanced": "Круто! Развитие навыков — ключ к успеху 💪"
            },
            "q2": {
                "learn": "Мудрый подход! Знания — основа успеха 🎓",
                "income": "Отличная цель! Стабильность важна 💰",
                "returns": "Амбициозно! Высокие цели требуют знаний 🚀"
            },
            "q3": {
                "conservative": "Разумно! Сохранение капитала — приоритет 🛡️",
                "moderate": "Сбалансированный подход! 👌",
                "aggressive": "Смело! Главное — управлять рисками 🎯"
            },
            "q4": {
                "casual": "Понятно! Есть стратегии для занятых людей ⏰",
                "parttime": "Хорошо! Этого времени достаточно для роста 📅",
                "fulltime": "Супер! С таким подходом результаты не заставят ждать 🚀"
            },
            "q5": {
                "small": "Отличное начало! Главное — правильные знания 💡",
                "medium": "Хорошая сумма для серьезного старта 💎",
                "large": "Внушительный капитал! Важно его правильно приумножить 💸"
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
            return f"""Анкета пройдена: {summary['segment_description']} ({summary['total_score']}/15 баллов)
Профиль: {summary['profile_summary']}"""
            
        except Exception as e:
            self.logger.error("Error getting survey summary", error=str(e))
            return None