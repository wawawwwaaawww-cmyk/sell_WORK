"""Consultation scheduling service."""

from datetime import datetime, date, time, timedelta
from typing import List, Optional, Tuple, Dict

import pytz
import structlog
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from dateutil.parser import parse as parse_datetime

from app.services.scheduler_service import scheduler_service
from app.models import Appointment, AppointmentStatus, User, AttendanceStatus
from app.services.lead_service import LeadService


class ConsultationRepository:
    """Repository for consultation database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()

    async def get_appointment_by_id(self, appointment_id: int) -> Optional[Appointment]:
        """Get an appointment by its ID."""
        stmt = select(Appointment).where(Appointment.id == appointment_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def create_appointment(
        self,
        user_id: int,
        user_name: str,
        appointment_date: date,
        slot: time,
        slot_utc: datetime,
        source: str,
        timezone: str = "Europe/Moscow",
    ) -> Appointment:
        """Create a new appointment."""
        appointment = Appointment(
            user_id=user_id,
            user_name=user_name,
            date=appointment_date,
            slot=slot,
            tz=timezone,
            status=AppointmentStatus.SCHEDULED,
            slot_utc=slot_utc,
            source=source,
            attendance=AttendanceStatus.PENDING,
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

    async def update_attendance(
        self, appointment: Appointment, attendance: AttendanceStatus
    ) -> Appointment:
        """Update appointment attendance status."""
        appointment.attendance = attendance
        await self.session.flush()
        await self.session.refresh(appointment)
        self.logger.info(
            "Appointment attendance updated",
            appointment_id=appointment.id,
            attendance=attendance,
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
    
    def _get_now_msk(self) -> datetime:
        """Get current time in Moscow timezone."""
        return datetime.now(self.moscow_tz)

    def get_consultation_date_options(self) -> List[Dict[str, any]]:
        """Get date choices based on the 17:45 MSK rule."""
        now_msk = self._get_now_msk()
        today = now_msk.date()
        
        options = []
        
        # Rule: if it's before 17:45 MSK, offer today.
        if now_msk.time() < time(17, 45):
            options.append({"label": f"Сегодня, {today.strftime('%d %b (%a)')}", "date": today})
            tomorrow = today + timedelta(days=1)
            options.append({"label": f"Завтра, {tomorrow.strftime('%d %b (%a)')}", "date": tomorrow})
        else:
            tomorrow = today + timedelta(days=1)
            options.append({"label": f"Завтра, {tomorrow.strftime('%d %b (%a)')}", "date": tomorrow})
            after_tomorrow = today + timedelta(days=2)
            options.append({"label": f"Послезавтра, {after_tomorrow.strftime('%d %b (%a)')}", "date": after_tomorrow})
            
        return options

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
        # All slots remain available regardless of existing bookings
        return list(self.available_slots)
    
    async def book_consultation(
        self,
        user_id: int,
        user_name: str,
        consultation_date: date,
        slot: time,
        source: str = "bot_consultation",
    ) -> Tuple[bool, Optional[Appointment], str]:
        """Book a consultation."""
        try:
            # Combine date and time with Moscow timezone
            dt_msk = self.moscow_tz.localize(datetime.combine(consultation_date, slot))
            dt_utc = dt_msk.astimezone(pytz.utc)

            # Validate date (not in the past, not weekend)
            if dt_msk < self._get_now_msk():
                return False, None, "Нельзя записаться на прошедшее время"

            if consultation_date.weekday() >= 5:
                return False, None, "Консультации проводятся только в будние дни"
            
            # Validate time slot
            if slot not in self.available_slots:
                return False, None, "Выбранное время недоступно"
            
            # Check if user already has an upcoming appointment
            user_upcoming = await self.repository.get_upcoming_appointments(user_id)
            if user_upcoming:
                return False, None, "У вас уже есть запланированная консультация"
            
            # Create appointment
            appointment = await self.repository.create_appointment(
                user_id=user_id,
                user_name=user_name,
                appointment_date=consultation_date,
                slot=slot,
                slot_utc=dt_utc,
                source=source,
            )
            
            # Schedule reminder using UTC time
            reminder_datetime_utc = dt_utc - timedelta(minutes=15)
            if reminder_datetime_utc > datetime.now(pytz.utc):
                await scheduler_service.schedule_appointment_reminder(
                    appointment, reminder_datetime_utc
                )

            # If booking is successful, complete the lead
            lead_service = LeadService(self.session)
            user_leads = await lead_service.repository.get_user_leads(user_id)
            draft_lead = next((l for l in user_leads if l.status == LeadStatus.DRAFT), None)
            if draft_lead:
                summary = await lead_service._generate_lead_summary(appointment.user, "consultation_booked")
                await lead_service.complete_lead(draft_lead, summary, LeadStatus.SCHEDULED)

            return True, appointment, "Консультация успешно запланирована"
            
        except Exception as e:
            self.logger.error("Error booking consultation", error=str(e), user_id=user_id, exc_info=True)
            return False, None, "Произошла ошибка при записи"
    
    async def process_reminder_response(
        self, appointment_id: int, action: str
    ) -> Optional[Appointment]:
        """Process user's response from a reminder."""
        appointment = await self.repository.get_appointment_by_id(appointment_id)
        if not appointment:
            return None

        if action == "confirm":
            await self.repository.update_attendance(
                appointment, AttendanceStatus.CONFIRMED
            )
        elif action == "cancel":
            await self.cancel_appointment(appointment)
            await self.repository.update_attendance(
                appointment, AttendanceStatus.CANCELED
            )
        
        return appointment

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

    def parse_free_text_datetime(self, text: str) -> Tuple[Optional[datetime], str]:
        """Parse free text input into a Moscow datetime object."""
        now_msk = self._get_now_msk()
        
        # Replace common phrases and clean up input
        text = text.lower().replace("на ", "").replace(" в ", " ")
        if "сегодня" in text:
            text = text.replace("сегодня", now_msk.strftime("%d.%m.%Y"))
        elif "завтра" in text:
            tomorrow = now_msk + timedelta(days=1)
            text = text.replace("завтра", tomorrow.strftime("%d.%m.%Y"))
        elif "послезавтра" in text:
            after_tomorrow = now_msk + timedelta(days=2)
            text = text.replace("послезавтра", after_tomorrow.strftime("%d.%m.%Y"))

        try:
            # Let dateutil do the heavy lifting
            parsed_dt = parse_datetime(text, dayfirst=True, default=now_msk.replace(second=0, microsecond=0))
            
            # Localize to Moscow time
            if parsed_dt.tzinfo is None:
                parsed_dt = self.moscow_tz.localize(parsed_dt)
            else:
                parsed_dt = parsed_dt.astimezone(self.moscow_tz)

            # --- Validations ---
            # 1. Not in the past (with 45 min gap)
            if parsed_dt < now_msk + timedelta(minutes=45):
                return None, "Это время уже прошло или слишком близко. Пожалуйста, выберите время как минимум через 45 минут."

            # 2. Time step >= 15 minutes
            if parsed_dt.minute % 15 != 0:
                return None, "Пожалуйста, выберите время с шагом в 15 минут (например, 16:00, 16:15, 16:30)."

            return parsed_dt, "Дата и время приняты."

        except (ValueError, TypeError) as e:
            self.logger.warning(
                "datetime_parse_failed",
                raw_text=text,
                error=str(e),
            )
            return None, "Не удалось распознать дату и время. Попробуйте формат 'ДД.ММ ЧЧ:ММ', например: '15.10 16:30'."
