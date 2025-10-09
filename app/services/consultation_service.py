"""Consultation scheduling service."""

from datetime import datetime, date, time, timedelta
from typing import List, Optional, Tuple

import pytz
import structlog
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.scheduler_service import scheduler_service
from app.models import Appointment, AppointmentStatus, User


class ConsultationRepository:
    """Repository for consultation database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def create_appointment(
        self,
        user_id: int,
        appointment_date: date,
        slot: time,
        timezone: str = "Europe/Moscow"
    ) -> Appointment:
        """Create a new appointment."""
        appointment = Appointment(
            user_id=user_id,
            date=appointment_date,
            slot=slot,
            tz=timezone,
            status=AppointmentStatus.SCHEDULED
        )
        
        self.session.add(appointment)
        await self.session.flush()
        await self.session.refresh(appointment)
        
        self.logger.info(
            "Appointment created",
            appointment_id=appointment.id,
            user_id=user_id,
            date=str(appointment_date),
            time=str(slot)
        )
        
        return appointment
    
    async def get_user_appointments(self, user_id: int) -> List[Appointment]:
        """Get all appointments for a user."""
        stmt = select(Appointment).where(
            Appointment.user_id == user_id
        ).order_by(Appointment.created_at.desc())
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_upcoming_appointments(self, user_id: int) -> List[Appointment]:
        """Get upcoming appointments for a user."""
        today = date.today()
        
        stmt = select(Appointment).where(
            and_(
                Appointment.user_id == user_id,
                Appointment.date >= today,
                Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED])
            )
        ).order_by(Appointment.date, Appointment.slot)
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def get_appointments_by_date_and_time(
        self,
        appointment_date: date,
        slot: time
    ) -> List[Appointment]:
        """Get appointments for specific date and time."""
        stmt = select(Appointment).where(
            and_(
                Appointment.date == appointment_date,
                Appointment.slot == slot,
                Appointment.status.in_([AppointmentStatus.SCHEDULED, AppointmentStatus.RESCHEDULED])
            )
        )
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    async def update_appointment_status(
        self,
        appointment: Appointment,
        status: AppointmentStatus
    ) -> Appointment:
        """Update appointment status."""
        appointment.status = status
        
        await self.session.flush()
        await self.session.refresh(appointment)
        
        self.logger.info(
            "Appointment status updated",
            appointment_id=appointment.id,
            status=status
        )
        
        return appointment
    
    async def reschedule_appointment(
        self,
        appointment: Appointment,
        new_date: date,
        new_slot: time
    ) -> Appointment:
        """Reschedule an appointment."""
        appointment.date = new_date
        appointment.slot = new_slot
        appointment.status = AppointmentStatus.RESCHEDULED
        
        await self.session.flush()
        await self.session.refresh(appointment)
        
        self.logger.info(
            "Appointment rescheduled",
            appointment_id=appointment.id,
            new_date=str(new_date),
            new_time=str(new_slot)
        )
        
        return appointment


class ConsultationService:
    """Service for consultation scheduling logic."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = ConsultationRepository(session)
        self.logger = structlog.get_logger()
        
        # Available time slots (Moscow time)
        self.available_slots = [
            time(12, 0),  # 12:00
            time(14, 0),  # 14:00
            time(16, 0),  # 16:00
            time(18, 0),  # 18:00
        ]
        
        # Moscow timezone
        self.moscow_tz = pytz.timezone("Europe/Moscow")
    
    def get_next_available_dates(self, days_ahead: int = 14) -> List[date]:
        """Get next available dates (excluding weekends)."""
        available_dates = []
        current_date = date.today() + timedelta(days=1)  # Start from tomorrow
        
        while len(available_dates) < days_ahead:
            # Skip weekends (Saturday=5, Sunday=6)
            if current_date.weekday() < 5:
                available_dates.append(current_date)
            current_date += timedelta(days=1)
        
        return available_dates
    
    async def get_available_slots_for_date(self, consultation_date: date) -> List[time]:
        """Get available time slots for a specific date."""
        available_slots = []
        
        for slot in self.available_slots:
            # Check if slot is already booked
            appointments = await self.repository.get_appointments_by_date_and_time(
                consultation_date, slot
            )
            
            # If no appointments or less than max capacity, slot is available
            # For simplicity, we allow only 1 appointment per slot
            if len(appointments) == 0:
                available_slots.append(slot)
        
        return available_slots
    
    async def book_consultation(
        self,
        user_id: int,
        consultation_date: date,
        slot: time
    ) -> Tuple[bool, Optional[Appointment], str]:
        """Book a consultation."""
        try:
            # Validate date (not in the past, not weekend)
            if consultation_date <= date.today():
                return False, None, "Нельзя записаться на прошедшую дату"
            
            if consultation_date.weekday() >= 5:
                return False, None, "Консультации проводятся только в будние дни"
            
            # Validate time slot
            if slot not in self.available_slots:
                return False, None, "Выбранное время недоступно"
            
            # Check if slot is available
            existing_appointments = await self.repository.get_appointments_by_date_and_time(
                consultation_date, slot
            )
            
            if existing_appointments:
                return False, None, "Это время уже занято"
            
            # Check if user already has an upcoming appointment
            user_upcoming = await self.repository.get_upcoming_appointments(user_id)
            if user_upcoming:
                return False, None, "У вас уже есть запланированная консультация"
            
            # Create appointment
            appointment = await self.repository.create_appointment(
                user_id=user_id,
                appointment_date=consultation_date,
                slot=slot
            )
            
            reminder_datetime = datetime.combine(consultation_date, slot) - timedelta(minutes=15)
            if reminder_datetime > datetime.utcnow():
                await scheduler_service.schedule_appointment_reminder(
                    appointment.id,
                    reminder_datetime
                )

            return True, appointment, "Консультация успешно запланирована"
            
        except Exception as e:
            self.logger.error("Error booking consultation", error=str(e), user_id=user_id)
            return False, None, "Произошла ошибка при записи"
    
    async def cancel_appointment(self, appointment: Appointment) -> bool:
        """Cancel an appointment."""
        try:
            scheduler_service.cancel_job(appointment.reminder_job_id)
            await self.repository.update_appointment_status(
                appointment, AppointmentStatus.CANCELED
            )
            appointment.reminder_job_id = None
            return True
        except Exception as e:
            self.logger.error("Error canceling appointment", error=str(e))
            return False
    
    async def reschedule_appointment(
        self,
        appointment: Appointment,
        new_date: date,
        new_slot: time
    ) -> Tuple[bool, str]:
        """Reschedule an appointment."""
        try:
            # Validate new date and time
            if new_date <= date.today():
                return False, "Нельзя перенести на прошедшую дату"
            
            if new_date.weekday() >= 5:
                return False, "Консультации проводятся только в будние дни"
            
            if new_slot not in self.available_slots:
                return False, "Выбранное время недоступно"
            
            # Check if new slot is available
            existing_appointments = await self.repository.get_appointments_by_date_and_time(
                new_date, new_slot
            )
            
            if existing_appointments:
                return False, "Это время уже занято"
            
            # Reschedule
            scheduler_service.cancel_job(appointment.reminder_job_id)
            updated_appointment = await self.repository.reschedule_appointment(
                appointment, new_date, new_slot
            )

            new_reminder = datetime.combine(new_date, new_slot) - timedelta(minutes=15)
            if new_reminder > datetime.utcnow():
                await scheduler_service.schedule_appointment_reminder(
                    updated_appointment.id,
                    new_reminder
                )

            return True, "Консультация успешно перенесена"
            
        except Exception as e:
            self.logger.error("Error rescheduling appointment", error=str(e))
            return False, "Произошла ошибка при переносе"
    
    def format_appointment_details(self, appointment: Appointment) -> str:
        """Format appointment details for display."""
        moscow_dt = datetime.combine(appointment.date, appointment.slot)
        formatted_date = moscow_dt.strftime("%d.%m.%Y")
        formatted_time = moscow_dt.strftime("%H:%M")
        
        return f"""📅 **Детали консультации:**

🗓 **Дата:** {formatted_date}
⏰ **Время:** {formatted_time} (МСК)
📱 **Формат:** Голосовая связь в Telegram
⏱ **Длительность:** 15 минут
💼 **Эксперт:** Персональный консультант

📝 **Что будет на консультации:**
✅ Анализ твоих целей и возможностей
✅ Подбор оптимальной программы обучения
✅ Ответы на все вопросы
✅ Персональные рекомендации

⚠️ *За 15 минут до встречи придет напоминание*"""
    
    def get_time_slots_text(self) -> str:
        """Get formatted time slots text."""
        slots_text = "⏰ **Доступные время:**\n\n"
        for slot in self.available_slots:
            slots_text += f"• {slot.strftime('%H:%M')} МСК\n"
        return slots_text
    
    def format_slot_time(self, slot: time) -> str:
        """Format time slot for display."""
        return slot.strftime("%H:%M МСК")
