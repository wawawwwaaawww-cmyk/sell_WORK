"""Event repository for tracking user actions."""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

import structlog
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event


class EventRepository:
    """Repository for event database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def create_event(
        self,
        user_id: int,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None
    ) -> Event:
        """Create a new event."""
        event = Event(
            user_id=user_id,
            type=event_type,
            payload=payload or {}
        )
        self.session.add(event)
        await self.session.flush()
        await self.session.refresh(event)

        self.logger.info(
            "Event created",
            event_id=event.id,
            user_id=user_id,
            event_type=event_type
        )

        return event
    
    async def get_user_events(
        self,
        user_id: int,
        event_type: Optional[str] = None,
        limit: int = 50
    ) -> List[Event]:
        """Get events for a user."""
        stmt = select(Event).where(Event.user_id == user_id)
        
        if event_type:
            stmt = stmt.where(Event.type == event_type)
        
        stmt = stmt.order_by(Event.created_at.desc()).limit(limit)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_recent_events(
        self,
        user_id: int,
        hours: int = 24
    ) -> List[Event]:
        """Get recent events for a user."""
        since = datetime.utcnow() - timedelta(hours=hours)
        
        stmt = select(Event).where(
            and_(
                Event.user_id == user_id,
                Event.created_at >= since
            )
        ).order_by(Event.created_at.desc())
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def count_events_by_type(
        self,
        user_id: int,
        event_type: str,
        hours: Optional[int] = None
    ) -> int:
        """Count events of specific type for a user."""
        stmt = select(Event).where(
            and_(
                Event.user_id == user_id,
                Event.type == event_type
            )
        )
        
        if hours:
            since = datetime.utcnow() - timedelta(hours=hours)
            stmt = stmt.where(Event.created_at >= since)
        
        result = await self.session.execute(stmt)
        return len(result.scalars().all())


class EventService:
    """Service for event tracking and analytics."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = EventRepository(session)
        self.logger = structlog.get_logger()
    
    async def create_event(
        self,
        user_id: int,
        type: str,
        payload: Optional[Dict[str, Any]] = None
    ) -> Event:
        """Create a new event."""
        return await self.repository.create_event(user_id, type, payload)

    async def log_event(
        self,
        user_id: int,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None
    ) -> Event:
        """Log a user event."""
        return await self.repository.create_event(user_id, event_type, payload)
    
    async def log_message_sent(self, user_id: int, message_type: str, content: str) -> Event:
        """Log message sent event."""
        return await self.log_event(
            user_id=user_id,
            event_type="message_sent",
            payload={
                "message_type": message_type,
                "content_preview": content[:100],
                "content_length": len(content)
            }
        )
    
    async def log_button_click(self, user_id: int, button_data: str) -> Event:
        """Log button click event."""
        return await self.log_event(
            user_id=user_id,
            event_type="button_click",
            payload={"button_data": button_data}
        )
    
    async def log_survey_answer(
        self,
        user_id: int,
        question: str,
        answer: str,
        points: int
    ) -> Event:
        """Log survey answer event."""
        return await self.log_event(
            user_id=user_id,
            event_type="survey_answer",
            payload={
                "question": question,
                "answer": answer,
                "points": points
            }
        )
    
    async def log_consultation_booked(
        self,
        user_id: int,
        date: str,
        time: str
    ) -> Event:
        """Log consultation booking event."""
        return await self.log_event(
            user_id=user_id,
            event_type="consultation_booked",
            payload={
                "date": date,
                "time": time
            }
        )
    
    async def log_consultation_reminder_response(
        self, user_id: int, appointment_id: int, response: str
    ) -> Event:
        """Log user's response to a consultation reminder."""
        return await self.log_event(
            user_id=user_id,
            event_type="consultation_reminder_response",
            payload={"appointment_id": appointment_id, "response": response},
        )

    async def log_payment_initiated(
        self,
        user_id: int,
        product_id: int,
        amount: float
    ) -> Event:
        """Log payment initiation event."""
        return await self.log_event(
            user_id=user_id,
            event_type="payment_initiated",
            payload={
                "product_id": product_id,
                "amount": amount
            }
        )
    
    async def get_user_journey(self, user_id: int) -> List[Event]:
        """Get user's complete journey."""
        return await self.repository.get_user_events(user_id, limit=100)
    
    async def get_engagement_score(self, user_id: int, hours: int = 24) -> int:
        """Calculate user engagement score based on recent activity."""
        events = await self.repository.get_recent_events(user_id, hours)
        
        # Score different event types
        score_map = {
            "start_command": 1,
            "bonus_received": 2,
            "survey_answer": 3,
            "button_click": 1,
            "message_sent": 2,
            "consultation_booked": 10,
            "payment_initiated": 15,
        }
        
        total_score = 0
        for event in events:
            total_score += score_map.get(event.type, 1)
        
        return total_score
    
    async def is_user_active(self, user_id: int, hours: int = 24) -> bool:
        """Check if user has been active recently."""
        events = await self.repository.get_recent_events(user_id, hours)
        return len(events) > 0
