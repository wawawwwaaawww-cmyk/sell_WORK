"""Scheduler service for automated tasks."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pytz
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import and_, select
import random
from app.services.excel_material_service import excel_material_service

from app.config import settings
from app.db import get_db
from app.models import Appointment, Lead, User
from app.services.notification_service import NotificationService
from app.services.ab_testing_service import ABTestingService
from app.services.sentiment_service import sentiment_service


logger = logging.getLogger(__name__)

SCHEDULER_REGISTRY: Dict[str, AsyncIOScheduler] = {}
DEFAULT_SCHEDULER_ID = "scheduler_service_main"
_notification_service: Optional[NotificationService] = None
LEGACY_JOB_SIGNATURES: List[bytes] = [
    b"SchedulerService._send_daily_lead_reminders",
    b"SchedulerService._send_appointment_reminders",
    b"SchedulerService._follow_up_inactive_users",
    b"SchedulerService._process_ab_tests",
    b"SchedulerService._cleanup_orphan_jobs",
]


def get_notification_service() -> NotificationService:
    """Get a cached notification service instance."""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service


class SchedulerService:
    """Service for managing scheduled tasks."""

    def __init__(self):
        tz = pytz.timezone(settings.scheduler_timezone or "UTC")
        jobstores: Dict[str, SQLAlchemyJobStore] = {}

        if settings.database_url_sync:
            try:
                jobstores["default"] = SQLAlchemyJobStore(url=settings.database_url_sync)
            except Exception as exc:
                logger.warning(
                    "Failed to configure SQLAlchemyJobStore, falling back to in-memory store (error=%s)",
                    exc,
                )

        if jobstores:
            self.scheduler = AsyncIOScheduler(jobstores=jobstores, timezone=tz)
        else:
            self.scheduler = AsyncIOScheduler(timezone=tz)

        self.scheduler_id = DEFAULT_SCHEDULER_ID
        SCHEDULER_REGISTRY[self.scheduler_id] = self.scheduler
        self.timezone = tz

    def start(self):
        """Start the scheduler."""
        if self.scheduler.running:
            logger.debug("Scheduler already running")
            return

        try:
            self._purge_legacy_jobs()
            self.scheduler.add_job(
                send_daily_lead_reminders,
                IntervalTrigger(hours=24, timezone=self.timezone),
                id="daily_lead_reminders",
                replace_existing=True,
            )

            self.scheduler.add_job(
                send_appointment_reminders,
                IntervalTrigger(minutes=30, timezone=self.timezone),
                id="appointment_reminders",
                replace_existing=True,
            )

            self.scheduler.add_job(
                follow_up_inactive_users,
                IntervalTrigger(hours=6, timezone=self.timezone),
                id="inactive_user_followup",
                replace_existing=True,
            )

            self.scheduler.add_job(
                process_ab_tests,
                IntervalTrigger(hours=1, timezone=self.timezone),
                id="ab_test_processing",
                replace_existing=True,
            )

            self.scheduler.add_job(
                sentiment_service.reconcile,
                IntervalTrigger(hours=1, timezone=self.timezone),
                id="sentiment_reconcile",
                replace_existing=True,
            )

            self.scheduler.add_job(
                cleanup_orphan_jobs,
                IntervalTrigger(hours=12, timezone=self.timezone),
                id="scheduler_job_cleanup",
                kwargs={"scheduler_id": self.scheduler_id},
                replace_existing=True,
            )

            self.scheduler.start()
            logger.info(
                "Scheduler started successfully (timezone=%s)",
                self.timezone,
            )

            self.reschedule_excel_materials_mailing()

        except Exception as exc:
            logger.error("Error starting scheduler", exc_info=exc)
            raise

    def stop(self):
        """Stop the scheduler."""
        if not self.scheduler.running:
            return

        try:
            self.scheduler.shutdown()
            SCHEDULER_REGISTRY.pop(self.scheduler_id, None)
            logger.info("Scheduler stopped")
        except Exception as exc:
            logger.error("Error stopping scheduler", exc_info=exc)

    def _purge_legacy_jobs(self) -> None:
        """Remove jobs serialized with legacy bound methods."""
        jobstore = self.scheduler._jobstores.get("default")  # type: ignore[attr-defined]
        if not isinstance(jobstore, SQLAlchemyJobStore):
            return

        try:
            legacy_job_ids: List[str] = []
            jobstore.jobs_t.create(jobstore.engine, checkfirst=True)  # type: ignore[attr-defined]
            with jobstore.engine.begin() as connection:  # type: ignore[attr-defined]
                rows = connection.execute(
                    select(jobstore.jobs_t.c.id, jobstore.jobs_t.c.job_state)
                ).all()

            for job_id, job_state in rows:
                if job_state and any(signature in job_state for signature in LEGACY_JOB_SIGNATURES):
                    legacy_job_ids.append(job_id)

            if not legacy_job_ids:
                return

            with jobstore.engine.begin() as connection:  # type: ignore[attr-defined]
                delete_stmt = jobstore.jobs_t.delete().where(
                    jobstore.jobs_t.c.id.in_(legacy_job_ids)
                )
                connection.execute(delete_stmt)

            logger.warning(
                "Purged legacy scheduler jobs serialized with service instances (removed_job_ids=%s)",
                legacy_job_ids,
            )
        except Exception as exc:
            logger.error("Failed to purge legacy scheduler jobs", exc_info=exc)

    async def schedule_appointment_reminder(self, appointment_id: int, reminder_time: datetime):
        """Schedule an appointment reminder."""
        try:
            async for db in get_db():
                result = await db.execute(
                    select(Appointment).where(Appointment.id == appointment_id)
                )
                appointment = result.scalar_one_or_none()
                if not appointment:
                    raise ValueError(f"Appointment {appointment_id} not found")

                appointment_tz = pytz.timezone(appointment.tz or settings.scheduler_timezone or "UTC")

                if reminder_time.tzinfo is None:
                    localized_reminder = appointment_tz.localize(reminder_time)
                else:
                    localized_reminder = reminder_time.astimezone(appointment_tz)

                run_date = localized_reminder.astimezone(self.timezone)

                job = self.scheduler.add_job(
                    send_appointment_reminder,
                    trigger=DateTrigger(run_date=run_date, timezone=self.timezone),
                    args=[appointment_id],
                    id=f"appointment_reminder_{appointment_id}",
                    replace_existing=True,
                )

                appointment.reminder_job_id = job.id
                await db.flush()
                await db.commit()
                break

            logger.info(
                "Scheduled appointment reminder (appointment_id=%s, run_at=%s, job_id=%s)",
                appointment_id,
                run_date,
                job.id,
            )

        except Exception as exc:
            logger.error(
                "Error scheduling appointment reminder (appointment_id=%s)",
                appointment_id,
                exc_info=exc,
            )
            raise

    async def schedule_lead_followup(self, lead_id: int, followup_time: datetime):
        """Schedule a lead follow-up."""
        try:
            if followup_time.tzinfo is None:
                run_date = self.timezone.localize(followup_time)
            else:
                run_date = followup_time.astimezone(self.timezone)

            job = self.scheduler.add_job(
                send_lead_followup,
                trigger=DateTrigger(run_date=run_date, timezone=self.timezone),
                args=[lead_id],
                id=f"lead_followup_{lead_id}",
                replace_existing=True,
            )

            logger.info(
                "Scheduled lead follow-up (lead_id=%s, run_at=%s, job_id=%s)",
                lead_id,
                run_date,
                job.id,
            )

        except Exception as exc:
            logger.error(
                "Error scheduling lead follow-up (lead_id=%s)",
                lead_id,
                exc_info=exc,
            )
            raise

    async def schedule_broadcast(self, broadcast_id: int, run_time: datetime) -> str:
        """Schedule a broadcast delivery."""
        try:
            if run_time.tzinfo is None:
                run_date = self.timezone.localize(run_time)
            else:
                run_date = run_time.astimezone(self.timezone)

            job = self.scheduler.add_job(
                send_scheduled_broadcast,
                trigger=DateTrigger(run_date=run_date, timezone=self.timezone),
                args=[broadcast_id],
                id=f"broadcast_send_{broadcast_id}",
                replace_existing=True,
            )

            logger.info(
                "Scheduled broadcast delivery (broadcast_id=%s, run_at=%s, job_id=%s)",
                broadcast_id,
                run_date,
                job.id,
            )
            return job.id

        except Exception as exc:
            logger.error(
                "Error scheduling broadcast (broadcast_id=%s)",
                broadcast_id,
                exc_info=exc,
            )
            raise

    async def schedule_ab_test_summary(self, test_id: int, summary_time: datetime) -> Optional[str]:
        """Schedule summary notification for A/B test."""
        try:
            if summary_time.tzinfo is None:
                run_date = self.timezone.localize(summary_time)
            else:
                run_date = summary_time.astimezone(self.timezone)

            job = self.scheduler.add_job(
                send_ab_test_summary,
                trigger=DateTrigger(run_date=run_date, timezone=self.timezone),
                args=[test_id],
                id=f"ab_test_summary_{test_id}",
                replace_existing=True,
            )

            logger.info(
                "Scheduled A/B test summary",
                test_id=test_id,
                run_at=run_date.isoformat(),
                job_id=job.id,
            )
            return job.id

        except Exception as exc:
            logger.error(
                "Error scheduling A/B test summary (test_id=%s)",
                test_id,
                exc_info=exc,
            )
            return None


    def cancel_job(self, job_id: Optional[str]) -> None:
        """Cancel a scheduled job if it exists."""
        if not job_id:
            return
        try:
            self.scheduler.remove_job(job_id)
        except JobLookupError:
            logger.debug("Scheduler job not found (job_id=%s)", job_id)
        except Exception as exc:
            logger.warning(
                "Failed to cancel job (job_id=%s, error=%s)",
                job_id,
                exc,
            )

    def get_excel_material_job_id(self, suffix: str) -> str:
       return f"excel_material_mailing_{suffix}"

    def reschedule_excel_materials_mailing(self):
       """Schedules or re-schedules the mailing based on config."""
       config = excel_material_service.get_schedule_config()
       
       # Remove all existing mailing jobs first to ensure clean state
       for i in range(2): # Max 2 jobs for "2 times a day"
           job_id = self.get_excel_material_job_id(str(i))
           try:
               self.scheduler.remove_job(job_id)
           except JobLookupError:
               pass # Job doesn't exist, which is fine

       if config.get('paused'):
           logger.info("Excel material mailing is paused. No jobs scheduled.")
           return

       freq = config.get('frequency', 'daily_1')
       start_h = config.get('window_start_h_msk', 11)
       end_h = config.get('window_end_h_msk', 20)

       # Convert MSK hours to UTC for the scheduler
       # Moscow is UTC+3
       start_h_utc = (start_h - 3 + 24) % 24
       end_h_utc = (end_h - 3 + 24) % 24

       if freq == 'daily_1':
           hour = random.randint(start_h_utc, end_h_utc -1)
           minute = random.randint(0, 59)
           trigger = CronTrigger(hour=hour, minute=minute, timezone='UTC')
           self.scheduler.add_job(
               dispatch_excel_material_mailing,
               trigger=trigger,
               id=self.get_excel_material_job_id("0"),
               replace_existing=True,
           )
           logger.info(f"Scheduled daily excel material mailing at {hour:02d}:{minute:02d} UTC")

       elif freq == 'daily_2':
           # Schedule two different random times
           for i in range(2):
               hour = random.randint(start_h_utc, end_h_utc - 1)
               minute = random.randint(0, 59)
               trigger = CronTrigger(hour=hour, minute=minute, timezone='UTC')
               self.scheduler.add_job(
                   dispatch_excel_material_mailing,
                   trigger=trigger,
                   id=self.get_excel_material_job_id(str(i)),
                   replace_existing=True,
               )
               logger.info(f"Scheduled twice-daily excel material mailing #{i+1} at {hour:02d}:{minute:02d} UTC")

       elif freq.startswith('every_'):
           days = int(freq.split('_')[1])
           hour = random.randint(start_h_utc, end_h_utc - 1)
           minute = random.randint(0, 59)
           trigger = CronTrigger(day=f"*/{days}", hour=hour, minute=minute, timezone='UTC')
           self.scheduler.add_job(
               dispatch_excel_material_mailing,
               trigger=trigger,
               id=self.get_excel_material_job_id("0"),
               replace_existing=True,
           )
           logger.info(f"Scheduled excel material mailing every {days} days at {hour:02d}:{minute:02d} UTC")

       elif freq == 'weekly':
           # Default to a random day of the week if not set
           day_of_week = config.get('base_day_of_week', random.randint(0, 6))
           hour = random.randint(start_h_utc, end_h_utc - 1)
           minute = random.randint(0, 59)
           trigger = CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute, timezone='UTC')
           self.scheduler.add_job(
               dispatch_excel_material_mailing,
               trigger=trigger,
               id=self.get_excel_material_job_id("0"),
               replace_existing=True,
           )
           logger.info(f"Scheduled weekly excel material mailing on day {day_of_week} at {hour:02d}:{minute:02d} UTC")

# Background job implementations


async def dispatch_excel_material_mailing():
    """The actual job that sends one material to all active users."""
    from app.bot import bot
    logger.info("Starting excel material mailing dispatch...")
    
    try:
        async for db in get_db():
            # Get all active users
            result = await db.execute(
                select(User).where(User.is_blocked == False)
            )
            active_users = result.scalars().all()
            break # Exit async generator
        if not active_users:
            logger.info("No active users found to send materials to.")
            return
        total_users = len(active_users)
        success_count = 0
        fail_count = 0
        for user in active_users:
            material = excel_material_service.get_next_material_for_user(user.id)
            
            if not material:
                logger.warning(f"No next material found for user {user.id}. Skipping.")
                excel_material_service.log_send_attempt(
                    user_id=user.id,
                    username=user.username,
                    material=None, # Or a dummy material object
                    status='skipped',
                    error='No valid material available'
                )
                fail_count += 1
                continue
            try:
                from aiogram.types import FSInputFile
                
                caption = material.text
                
                if material.media_type == 'photo':
                    await bot.send_photo(
                        chat_id=user.telegram_id,
                        photo=FSInputFile(material.media_path),
                        caption=caption
                    )
                elif material.media_type == 'video':
                    await bot.send_video(
                        chat_id=user.telegram_id,
                        video=FSInputFile(material.media_path),
                        caption=caption
                    )
                
                excel_material_service.update_user_progress(user.id, material.row_index)
                excel_material_service.log_send_attempt(
                    user_id=user.id,
                    username=user.username,
                    material=material,
                    status='success'
                )
                success_count += 1
                
            except Exception as e:
                logger.error(f"Failed to send material to user {user.id}: {e}", exc_info=True)
                excel_material_service.log_send_attempt(
                    user_id=user.id,
                    username=user.username,
                    material=material,
                    status='failed',
                    error=str(e)
                )
                fail_count += 1
        
        logger.info(
            f"Excel material mailing finished. Total: {total_users}, Success: {success_count}, Failed: {fail_count}"
        )
    except Exception as e:
        logger.error(f"Critical error in dispatch_excel_material_mailing: {e}", exc_info=True)

async def send_scheduled_broadcast(broadcast_id: int) -> None:
    """Deliver a scheduled broadcast."""
    try:
        notification_service = get_notification_service()
        async for db in get_db():
            from app.services.broadcast_service import BroadcastService
            broadcast_service = BroadcastService(notification_service.bot, db)
            result = await broadcast_service.send_simple_broadcast(broadcast_id)
            await db.commit()
            logger.info(
                "Scheduled broadcast sent (broadcast_id=%s, result=%s)",
                broadcast_id,
                result,
            )
            break
    except Exception as exc:
        logger.error(
            "Error sending scheduled broadcast (broadcast_id=%s)",
            broadcast_id,
            exc_info=exc,
        )


async def send_daily_lead_reminders() -> None:
    """Send daily reminders about pending leads to managers."""
    try:
        notification_service = get_notification_service()
        async for db in get_db():
            result = await db.execute(
                select(Lead, User.telegram_id, User.first_name, User.last_name)
                .join(User)
                .where(Lead.status == "new")
            )
            pending_leads = result.all()

            if not pending_leads:
                return

            leads_by_manager: Dict[Optional[int], List] = {}
            for lead, telegram_id, first_name, last_name in pending_leads:
                manager_id = lead.assigned_manager_id
                leads_by_manager.setdefault(manager_id, [])
                full_name = f"{first_name or ''} {last_name or ''}".strip() or str(telegram_id)
                leads_by_manager[manager_id].append((lead, telegram_id, full_name))

            for manager_id, leads in leads_by_manager.items():
                if manager_id:
                    await notification_service.send_lead_reminder(manager_id, leads)
            break

    except Exception as exc:
        logger.error("Error sending daily lead reminders", exc_info=exc)


async def send_appointment_reminders() -> None:
    """Send appointment reminders to users."""
    try:
        notification_service = get_notification_service()
        async for db in get_db():
            now_utc = datetime.now(timezone.utc)

            result = await db.execute(
                select(Appointment, User.telegram_id)
                .join(User)
                .where(Appointment.status == "scheduled")
            )
            appointments = result.all()

            for appointment, telegram_id in appointments:
                appointment_tz = pytz.timezone(
                    appointment.tz or settings.scheduler_timezone or "UTC"
                )
                scheduled_local = appointment_tz.localize(
                    datetime.combine(appointment.date, appointment.slot)
                )
                time_until_hours = (
                    scheduled_local - now_utc.astimezone(appointment_tz)
                ).total_seconds() / 3600

                if 0 < time_until_hours <= 2:
                    await notification_service.send_consultation_reminder(
                        telegram_id,
                        scheduled_local,
                    )
            break

    except Exception as exc:
        logger.error("Error sending consultation reminders", exc_info=exc)


async def follow_up_inactive_users() -> None:
    """Follow up with users who haven't been active."""
    try:
        notification_service = get_notification_service()
        now_utc = datetime.now(timezone.utc)
        inactive_since = now_utc - timedelta(days=3)
        inactive_before = now_utc - timedelta(days=7)

        async for db in get_db():
            result = await db.execute(
                select(User.telegram_id, User.segment, User.updated_at)
                .where(
                    and_(
                        User.updated_at <= inactive_since,
                        User.updated_at >= inactive_before,
                        User.is_blocked.is_(False),
                    )
                )
            )
            inactive_users = result.all()

            for telegram_id, segment, _ in inactive_users:
                await notification_service.send_reengagement_message(
                    telegram_id, segment or "warm"
                )
            break

    except Exception as exc:
        logger.error("Error following up inactive users", exc_info=exc)


async def send_appointment_reminder(appointment_id: int) -> None:
    """Send individual appointment reminder."""
    try:
        notification_service = get_notification_service()
        async for db in get_db():
            result = await db.execute(
                select(Appointment, User.telegram_id)
                .join(User)
                .where(Appointment.id == appointment_id)
            )
            appointment_data = result.first()

            if appointment_data:
                appointment, telegram_id = appointment_data
                appointment_tz = pytz.timezone(
                    appointment.tz or settings.scheduler_timezone or "UTC"
                )
                reminder_datetime = appointment_tz.localize(
                    datetime.combine(appointment.date, appointment.slot)
                )
                await notification_service.send_consultation_reminder(
                    user_id=telegram_id,
                    appointment_id=appointment.id,
                    consultation_time=reminder_datetime,
                )
                appointment.reminder_job_id = None
                await db.flush()
                await db.commit()
            break

    except Exception as exc:
        logger.error(
            "Error sending appointment reminder", appointment_id=appointment_id, exc_info=exc
        )


async def send_lead_followup(lead_id: int) -> None:
    """Send individual lead follow-up."""
    try:
        notification_service = get_notification_service()
        async for db in get_db():
            result = await db.execute(
                select(Lead, User.telegram_id, User.first_name, User.last_name)
                .join(User)
                .where(Lead.id == lead_id)
            )
            lead_data = result.first()

            if lead_data:
                lead, telegram_id, first_name, last_name = lead_data
                full_name = f"{first_name or ''} {last_name or ''}".strip() or str(telegram_id)

                if lead.assigned_manager_id:
                    await notification_service.send_lead_followup(
                        lead.assigned_manager_id,
                        lead,
                        telegram_id,
                        full_name,
                    )
            break

    except Exception as exc:
        logger.error(
            "Error sending lead follow-up (lead_id=%s)",
            lead_id,
            exc_info=exc,
        )


async def process_ab_tests() -> None:
    """Check running A/B tests and finalize deliveries."""
    try:
        notification_service = get_notification_service()
        async for db in get_db():
            ab_service = ABTestingService(db)
            running_tests = await ab_service.get_running_tests()

            if not running_tests:
                break

            for test in running_tests:
                try:
                    if not await ab_service.should_complete_test(test.id):
                        continue

                    success, detail, analysis = await ab_service.complete_test(test.id)
                    if not success:
                        logger.warning(
                            "A/B test completion failed (test_id=%s, detail=%s)",
                            test.id,
                            detail,
                        )
                        continue

                    await db.commit()

                    if analysis.get("winner"):
                        logger.info(
                            "A/B test winner available",
                            test_id=test.id,
                            winner=analysis["winner"],
                        )
                    else:
                        logger.info(
                            "A/B test completed without winner (test_id=%s)",
                            test.id,
                        )
                except Exception as job_exc:
                    logger.error(
                        "Error during A/B test processing (test_id=%s)",
                        getattr(test, "id", None),
                        exc_info=job_exc,
                    )
            break

    except Exception as exc:
        logger.error("Error processing A/B tests", exc_info=exc)


async def send_ab_test_summary(test_id: int) -> None:
    """Aggregate A/B test metrics and notify initiator."""
    try:
        notification_service = get_notification_service()
        async for db in get_db():
            ab_service = ABTestingService(db)
            success, detail, analysis = await ab_service.complete_test(test_id)
            if not success:
                logger.warning(
                    "A/B test summary skipped",
                    test_id=test_id,
                    detail=detail,
                )
                await db.rollback()
                break

            creator_id = analysis.get("creator_user_id")
            if creator_id:
                await notification_service.send_ab_test_summary(creator_id, analysis)
            else:
                logger.warning(
                    "A/B test summary has no creator",
                    test_id=test_id,
                )

            await db.commit()
            break

    except Exception as exc:
        logger.error("Error sending A/B test summary", exc_info=exc)


async def cleanup_orphan_jobs(scheduler_id: str) -> None:
    """Remove finished or stale scheduler jobs."""
    try:
        scheduler = SCHEDULER_REGISTRY.get(scheduler_id)
        if scheduler is None:
            logger.warning(
                "Cleanup skipped: scheduler not registered (scheduler_id=%s)",
                scheduler_id,
            )
            return

        cutoff = datetime.now(timezone.utc) - timedelta(days=1)
        for job in list(scheduler.get_jobs()):
            next_run = job.next_run_time
            if next_run is None or next_run.astimezone(timezone.utc) < cutoff:
                try:
                    scheduler.remove_job(job.id)
                except JobLookupError:
                    logger.debug("Cleanup skipped: job not found (job_id=%s)", job.id)
    except Exception as exc:
        logger.warning("Error during scheduler cleanup", exc_info=exc)


# Global scheduler instance
scheduler_service = SchedulerService()
