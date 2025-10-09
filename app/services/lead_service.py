"""Lead management service."""

from typing import List, Optional, Dict, Any

from datetime import datetime, timezone, date

import structlog
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Lead, LeadNote, LeadStatus, User
from app.config import settings


class LeadRepository:
    """Repository for lead database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def create_lead(
        self,
        user_id: int,
        summary: Optional[str] = None,
        *,
        handoff_trigger: Optional[str] = None,
        priority: int = 40,
        handoff_channel: str = 'bot'
    ) -> Lead:
        """Create a new lead with metadata."""
        lead = Lead(
            user_id=user_id,
            status=LeadStatus.NEW,
            summary=summary,
            handoff_trigger=handoff_trigger,
            priority=priority,
            handoff_channel=handoff_channel
        )
        self.session.add(lead)
        await self.session.flush()
        await self.session.refresh(lead)

        self.logger.info(
            "Lead created",
            lead_id=lead.id,
            user_id=user_id,
            handoff_trigger=handoff_trigger,
            priority=priority,
            handoff_channel=handoff_channel
        )

        return lead
    
    async def get_lead_by_id(self, lead_id: int) -> Optional[Lead]:
        """Get lead by ID."""
        stmt = select(Lead).where(Lead.id == lead_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_user_leads(self, user_id: int) -> List[Lead]:
        """Get all leads for a user."""
        stmt = select(Lead).where(
            Lead.user_id == user_id
        ).order_by(Lead.created_at.desc())
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_active_leads(self) -> List[Lead]:
        """Get all leads that are either new or already в работе."""
        stmt = select(Lead).where(
            Lead.status.in_([LeadStatus.NEW, LeadStatus.TAKEN])
        ).order_by(Lead.created_at.desc())
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def assign_lead_to_manager(
        self,
        lead: Lead,
        manager_id: int
    ) -> Lead:
        """Assign lead to a manager and timestamp the takeover."""
        lead.status = LeadStatus.TAKEN
        lead.assigned_manager_id = manager_id
        lead.taken_at = datetime.now(timezone.utc)

        await self.session.flush()
        await self.session.refresh(lead)

        self.logger.info(
            "Lead assigned to manager",
            lead_id=lead.id,
            manager_id=manager_id
        )

        return lead
    
    async def update_lead_status(
        self,
        lead: Lead,
        status: LeadStatus
    ) -> Lead:
        """Update lead status."""
        lead.status = status
        
        await self.session.flush()
        await self.session.refresh(lead)
        
        self.logger.info(
            "Lead status updated",
            lead_id=lead.id,
            status=status
        )

        return lead

    async def return_to_queue(self, lead: Lead) -> Lead:
        """Return lead back to common queue."""
        lead.status = LeadStatus.NEW
        lead.assigned_manager_id = None
        lead.taken_at = None
        lead.closed_at = None
        lead.close_reason = None

        await self.session.flush()
        await self.session.refresh(lead)

        self.logger.info(
            "Lead returned to queue",
            lead_id=lead.id,
        )

        return lead

    async def add_note(
        self,
        lead_id: int,
        note_text: str,
        *,
        author_id: Optional[int] = None,
        channel: Optional[str] = None
    ) -> "LeadNote":
        """Attach a note to a lead."""
        note = LeadNote(
            lead_id=lead_id,
            author_id=author_id,
            channel=channel,
            note_text=note_text
        )

        self.session.add(note)
        await self.session.flush()
        await self.session.refresh(note)

        self.logger.info(
            "Lead note created",
            lead_id=lead_id,
            author_id=author_id
        )

        return note


class LeadService:
    """Service for lead management logic."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = LeadRepository(session)
        self.logger = structlog.get_logger()
    
    async def should_create_lead(self, user: User, context: Dict[str, Any]) -> bool:
        """Determine if a lead should be created based on user behavior."""
        
        # Check if user already has an active lead
        existing_leads = await self.repository.get_user_leads(user.id)
        active_leads = [l for l in existing_leads if l.status in [LeadStatus.NEW, LeadStatus.TAKEN]]
        
        if active_leads:
            return False
        
        # Hot segment users with high engagement
        if user.segment == "hot" and user.lead_score >= 12:
            return True
        
        # User has booked consultation
        if context.get("consultation_booked"):
            return True
        
        # User has initiated payment
        if context.get("payment_initiated"):
            return True
        
        # User has requested manager contact
        if context.get("manager_requested"):
            return True
        
        # User has shown repeated interest (multiple material requests, etc.)
        if context.get("high_engagement_score", 0) >= 20:
            return True
        
        return False
    
    async def create_lead_from_user(
        self,
        user: User,
        trigger_event: str,
        conversation_summary: Optional[str] = None
    ) -> Lead:
        """Create a lead from user with summary."""
        
        # Generate summary if not provided
        if not conversation_summary:
            conversation_summary = await self._generate_lead_summary(user, trigger_event)
        
        priority = self._calculate_priority(user, trigger_event)
        lead = await self.repository.create_lead(
            user_id=user.id,
            summary=conversation_summary,
            handoff_trigger=trigger_event,
            priority=priority,
            handoff_channel='bot'
        )

        return lead
    
    async def create_lead(
        self,
        user_id: int,
        summary: str,
        trigger: str = "manual"
    ) -> Lead:
        """Create a lead with summary and trigger information."""
        try:
            # Add trigger information to summary
            enhanced_summary = f"Триггер: {trigger}\n\n{summary}"
            
            channel = 'manual' if trigger == 'manual' else 'bot'
            priority = 40

            user = await self.session.get(User, user_id)
            if user:
                priority = self._calculate_priority(user, trigger)

            lead = await self.repository.create_lead(
                user_id=user_id,
                summary=enhanced_summary,
                handoff_trigger=trigger,
                priority=priority,
                handoff_channel=channel,
            )

            self.logger.info(
                "Lead created via service",
                lead_id=lead.id,
                user_id=user_id,
                trigger=trigger,
                priority=priority,
                handoff_channel=channel,
            )

            return lead
            
        except Exception as e:
            self.logger.error("Error creating lead", error=str(e), user_id=user_id)
            raise
    
    async def _generate_lead_summary(self, user: User, trigger_event: str) -> str:
        """Generate lead summary based on user profile and trigger."""
        
        # Basic user info
        name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Не указано"
        username = f"@{user.username}" if user.username else "Не указан"
        
        # Survey data summary
        survey_info = "Анкета не пройдена"
        if user.segment:
            segment_names = {
                "cold": "Новичок",
                "warm": "Базовые знания", 
                "hot": "Продвинутый"
            }
            survey_info = f"Сегмент: {segment_names.get(user.segment, user.segment)} ({user.lead_score} баллов)"
        
        # Trigger event description
        trigger_descriptions = {
            "consultation_booked": "Записался на консультацию",
            "payment_initiated": "Инициировал оплату",
            "manager_requested": "Запросил связь с менеджером",
            "high_engagement": "Высокая активность в боте",
            "hot_segment": "Высокий балл готовности"
        }
        
        trigger_desc = trigger_descriptions.get(trigger_event, trigger_event)
        
        summary = f"""Лид создан: {trigger_desc}
        
Профиль:
• Имя: {name}
• Telegram: {username}
• Телефон: {'указан' if user.phone else 'не указан'}
• Email: {'указан' if user.email else 'не указан'}
• {survey_info}
• Этап воронки: {user.funnel_stage}

Готов к работе с менеджером."""
        
        return summary
    
    async def format_lead_card(self, lead: Lead, user: User) -> str:
        """Format lead card for manager channel."""
        
        # User display info
        name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Не указано"
        username = f"@{user.username}" if user.username else "Не указан"
        phone = user.phone if user.phone else "Не указан"
        email = user.email if user.email else "Не указан"

        # Segment heat and label
        heat_label = "⚪️ Не определён"
        segment_label = "Не определён"
        if user.segment:
            heat_map = {
                "cold": "❄️ Холодный",
                "warm": "🔥 Тёплый",
                "hot": "🚀 Горячий",
            }
            detail_map = {
                "cold": "Новичок",
                "warm": "Базовые знания",
                "hot": "Продвинутый",
            }
            heat_label = heat_map.get(user.segment, user.segment)
            segment_label = f"{detail_map.get(user.segment, user.segment)} ({user.lead_score} баллов)"

        # Funnel status
        status_map = {
            "consultation": "📅 Назначена консультация",
            "payment": "💳 Инициирован платеж",
            "engaged": "💬 Активный диалог",
        }
        status_info = status_map.get(user.funnel_stage, user.funnel_stage or "не указан")

        # Conversation summary snippet
        summary_raw = (lead.summary or "Сводка не сформирована").strip()
        summary_trimmed = summary_raw
        if len(summary_trimmed) > 400:
            summary_trimmed = summary_trimmed[:400].rstrip() + "…"

        lead_card = f"""👤 **Лид #{lead.id} — {heat_label}**

📋 **Профиль**
• Имя: {name}
• Telegram: {username}
• Телефон: {phone}
• Email: {email}
• Сегмент: {segment_label}
• Этап: {status_info}

📝 **Кратко по диалогу**
{summary_trimmed}

🕐 Создан: {lead.created_at.strftime('%d.%m.%Y %H:%M')}
📎 История переписки доступна по кнопке ниже
"""
        
        return lead_card
    
    async def assign_lead(self, lead_id: int, manager_id: int) -> tuple[bool, str]:
        """Assign lead to manager."""
        try:
            lead = await self.repository.get_lead_by_id(lead_id)
            if not lead:
                return False, "Лид не найден"

            if lead.status != LeadStatus.NEW:
                return False, "Лид уже взят другим менеджером"

            await self.repository.assign_lead_to_manager(lead, manager_id)

            await self.repository.add_note(
                lead_id=lead.id,
                note_text="Заявка взята в работу менеджером",
                author_id=manager_id,
                channel="manager_channel",
            )

            return True, "Лид успешно назначен"

        except Exception as e:
            self.logger.error("Error assigning lead", error=str(e), lead_id=lead_id)
            return False, "Ошибка при назначении лида"

    async def return_lead_to_queue(self, lead_id: int, manager_id: int) -> tuple[bool, str]:
        """Return lead back to queue by the currently assigned manager."""
        try:
            lead = await self.repository.get_lead_by_id(lead_id)
            if not lead:
                return False, "Лид не найден"

            if lead.status != LeadStatus.TAKEN:
                return False, "Лид ещё не в работе"

            if lead.assigned_manager_id and lead.assigned_manager_id != manager_id:
                return False, "Лид закреплён за другим менеджером"

            await self.repository.return_to_queue(lead)
            await self.repository.add_note(
                lead_id=lead.id,
                note_text="Заявка вернулась в очередь",
                author_id=manager_id,
                channel="manager_channel",
            )

            return True, "Лид возвращён в очередь"

        except Exception as e:
            self.logger.error("Error returning lead", error=str(e), lead_id=lead_id)
            return False, "Ошибка при возврате лида"

    async def get_manager_lead_details(self, lead: Lead, user: User) -> str:
        """Get detailed lead information for manager."""
        
        lead_card = await self.format_lead_card(lead, user)
        
        # Add manager-specific information
        manager_info = f"""

🎯 **Для менеджера:**
• **ID лида:** {lead.id}
• **ID пользователя:** {user.id}
• **Telegram ID:** {user.telegram_id}

💡 **Рекомендации:**
• Свяжитесь в течение 15 минут для максимальной конверсии
• Используйте информацию из сводки для персонализации
• При отказе зафиксируйте причину в CRM

🔗 **Действия:**
• Для перехвата диалога: /takeover {user.id}
• Для обновления статуса: /lead_status {lead.id} <статус>
"""
        
        return lead_card + manager_info
    
    async def get_lead_statistics(self) -> Dict[str, Any]:
        """Get lead statistics."""
        try:
            all_leads = await self.repository.get_active_leads()
            today: date = datetime.now(timezone.utc).date()

            stats = {
                "total_active": len(all_leads),
                "new_leads": len([l for l in all_leads if l.status == LeadStatus.NEW]),
                "taken_leads": len([l for l in all_leads if l.status == LeadStatus.TAKEN]),
                "leads_today": len([l for l in all_leads if l.created_at.date() == today])
            }

            return stats

        except Exception as e:
            self.logger.error("Error getting lead statistics", error=str(e))
            return {"error": "Failed to get statistics"}

    def _calculate_priority(self, user: User, trigger: str) -> int:
        """Calculate lead priority based on сегмент и триггер."""
        base_by_segment = {
            "hot": 100,
            "warm": 60,
            "cold": 20,
        }

        priority = base_by_segment.get(user.segment or "", 40)

        trigger_boosts = {
            "payment_initiated": 90,
            "payment_with_discount": 85,
            "consultation_booked": 80,
            "manager_requested": 70,
            "manual": 40,
        }

        priority = max(priority, trigger_boosts.get(trigger, priority))

        # Ensure manual creation never превышает дефолтный приоритет
        if trigger == "manual":
            return max(priority, 40)

        return priority
