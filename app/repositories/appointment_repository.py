"""Appointment repository for managing consultation scheduling."""

from typing import List, Optional, Dict, Any
from datetime import date, time, datetime, timedelta

import structlog
from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, AppointmentStatus, User


class AppointmentRepository:
    """Repository for appointment database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def create_appointment(
        self,
        user_id: int,
        appointment_date: date,
        appointment_slot: time,
        timezone: str = "Europe/Moscow",
        reminder_job_id: Optional[str] = None
    ) -> Appointment:
        """Create a new appointment."""
        appointment = Appointment(
            user_id=user_id,
            date=appointment_date,
            slot=appointment_slot,
            tz=timezone,
            status=AppointmentStatus.SCHEDULED,
            reminder_job_id=reminder_job_id
        )
        
        self.session.add(appointment)
        await self.session.flush()
        await self.session.refresh(appointment)
        
        self.logger.info(
            "Appointment created",
            appointment_id=appointment.id,
            user_id=user_id,
            date=appointment_date,
            slot=appointment_slot
        )
        
        return appointment
    
    async def get_by_id(self, appointment_id: int) -> Optional[Appointment]:
        """Get appointment by ID."""
        stmt = select(Appointment).where(Appointment.id == appointment_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def get_user_appointments(
        self,
        user_id: int,
        status: Optional[AppointmentStatus] = None,
        limit: int = 10
    ) -> List[Appointment]:
        """Get appointments for a specific user."""
        stmt = select(Appointment).where(Appointment.user_id == user_id)
        
        if status:
            stmt = stmt.where(Appointment.status == status)
        
        stmt = stmt.order_by(Appointment.date.desc(), Appointment.slot.desc()).limit(limit)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_available_slots(
        self,
        target_date: date,
        working_hours_start: time = time(9, 0),
        working_hours_end: time = time(18, 0),
        slot_duration_minutes: int = 60
    ) -> List[time]:
        """Get available time slots for a specific date."""
        
        # Get existing appointments for the date
        stmt = select(Appointment.slot).where(
            and_(
                Appointment.date == target_date,
                Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED])
            )
        )
        result = await self.session.execute(stmt)
        booked_slots = {row[0] for row in result.fetchall()}
        
        # Generate all possible slots
        all_slots = []
        current_time = datetime.combine(target_date, working_hours_start)
        end_time = datetime.combine(target_date, working_hours_end)
        
        while current_time < end_time:
            slot_time = current_time.time()
            if slot_time not in booked_slots:
                all_slots.append(slot_time)
            current_time += timedelta(minutes=slot_duration_minutes)
        
        return all_slots
    
    async def reschedule_appointment(
        self,
        appointment_id: int,
        new_date: date,
        new_slot: time
    ) -> Optional[Appointment]:
        """Reschedule an existing appointment."""
        appointment = await self.get_by_id(appointment_id)
        if not appointment:
            return None
        
        old_date = appointment.date
        old_slot = appointment.slot
        
        appointment.date = new_date
        appointment.slot = new_slot
        appointment.status = AppointmentStatus.RESCHEDULED
        
        await self.session.flush()
        
        self.logger.info(
            "Appointment rescheduled",
            appointment_id=appointment_id,
            old_date=old_date,
            old_slot=old_slot,
            new_date=new_date,
            new_slot=new_slot
        )
        
        return appointment
    
    async def cancel_appointment(self, appointment_id: int) -> bool:
        """Cancel an appointment."""
        appointment = await self.get_by_id(appointment_id)
        if not appointment:
            return False
        
        appointment.status = AppointmentStatus.CANCELED
        await self.session.flush()
        
        self.logger.info("Appointment canceled", appointment_id=appointment_id)
        return True
    
    async def complete_appointment(self, appointment_id: int) -> bool:
        """Mark appointment as completed."""
        appointment = await self.get_by_id(appointment_id)
        if not appointment:
            return False
        
        appointment.status = AppointmentStatus.COMPLETED
        await self.session.flush()
        
        self.logger.info("Appointment completed", appointment_id=appointment_id)
        return True
    
    async def get_upcoming_appointments(
        self,
        days_ahead: int = 7,
        status: Optional[AppointmentStatus] = None
    ) -> List[Appointment]:
        """Get upcoming appointments within specified days."""
        start_date = datetime.utcnow().date()
        end_date = start_date + timedelta(days=days_ahead)
        
        stmt = select(Appointment).where(
            and_(
                Appointment.date >= start_date,
                Appointment.date <= end_date
            )
        )
        
        if status:
            stmt = stmt.where(Appointment.status == status)
        
        stmt = stmt.order_by(Appointment.date, Appointment.slot)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_appointments_needing_reminders(
        self,
        hours_before: int = 24
    ) -> List[Appointment]:
        """Get appointments that need reminder notifications."""
        target_time = datetime.utcnow() + timedelta(hours=hours_before)
        target_date = target_time.date()
        target_slot = target_time.time()
        
        stmt = select(Appointment).where(
            and_(
                Appointment.date == target_date,
                Appointment.slot <= target_slot,
                Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED]),
                Appointment.reminder_job_id.is_(None)  # No reminder sent yet
            )
        )
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def update_reminder_job_id(self, appointment_id: int, job_id: str) -> bool:
        """Update reminder job ID for an appointment."""
        appointment = await self.get_by_id(appointment_id)
        if not appointment:
            return False
        
        appointment.reminder_job_id = job_id
        await self.session.flush()
        
        return True
    
    async def get_appointment_statistics(self, days: int = 30) -> Dict[str, Any]:
        """Get appointment statistics for the last N days."""
        start_date = datetime.utcnow().date() - timedelta(days=days)
        
        # Total appointments
        total_stmt = select(func.count(Appointment.id)).where(
            Appointment.date >= start_date
        )
        total_result = await self.session.execute(total_stmt)
        total_appointments = total_result.scalar()
        
        # Appointments by status
        status_stmt = select(
            Appointment.status,
            func.count(Appointment.id)
        ).where(
            Appointment.date >= start_date
        ).group_by(Appointment.status)
        
        status_result = await self.session.execute(status_stmt)
        status_stats = {row[0]: row[1] for row in status_result.fetchall()}
        
        # Most popular time slots
        slot_stmt = select(
            Appointment.slot,
            func.count(Appointment.id)
        ).where(
            Appointment.date >= start_date
        ).group_by(Appointment.slot).order_by(func.count(Appointment.id).desc()).limit(5)
        
        slot_result = await self.session.execute(slot_stmt)
        popular_slots = [(row[0].strftime("%H:%M"), row[1]) for row in slot_result.fetchall()]
        
        return {
            "total_appointments": total_appointments,
            "by_status": status_stats,
            "popular_time_slots": popular_slots,
            "period_days": days
        }