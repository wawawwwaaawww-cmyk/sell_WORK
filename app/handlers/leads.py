"""Lead management handlers."""

import structlog
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import User
from app.services.lead_service import LeadService
from app.services.manager_notification_service import ManagerNotificationService
from app.services.event_service import EventService
from app.repositories.admin_repository import AdminRepository


router = Router()
logger = structlog.get_logger()


@router.callback_query(F.data.startswith("manager:request"))
async def handle_manager_request(callback: CallbackQuery, user: User, **kwargs):
    """Handle manager contact request."""
    try:
        # Log event
        event_service = EventService(kwargs.get("session"))
        await event_service.log_event(
            user_id=user.id,
            event_type="manager_requested",
            payload={}
        )
        
        # Check if should create lead
        lead_service = LeadService(kwargs.get("session"))
        context = {"manager_requested": True}
        
        if await lead_service.should_create_lead(user, context):
            # Create lead
            lead = await lead_service.create_lead_from_user(
                user=user,
                trigger_event="manager_requested",
                conversation_summary="Пользователь запросил связь с менеджером"
            )
            
            # Notify managers
            manager_service = ManagerNotificationService(callback.bot, kwargs.get("session"))
            await manager_service.notify_new_lead(lead, user)
            await manager_service.notify_manager_request(user)
            
            response_text = """👤 **Запрос отправлен менеджеру!**

✅ Твоя заявка передана нашим менеджерам
⏰ Мы свяжемся с тобой в течение 15 минут
📱 Ожидай сообщение в этом чате

💡 *Пока ожидаешь, можешь изучить дополнительные материалы или задать вопросы боту*

Спасибо за интерес к нашим программам! 🚀"""
            
        else:
            # Just notify without creating lead
            manager_service = ManagerNotificationService(callback.bot, kwargs.get("session"))
            await manager_service.notify_manager_request(user)
            
            response_text = """👤 **Запрос отправлен!**

📬 Уведомление отправлено нашим менеджерам
⏰ Мы свяжемся с тобой в ближайшее время
📱 Следи за сообщениями в этом чате

Есть вопросы? Пиши боту! 💬"""
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="📚 Полезные материалы",
            callback_data="materials:educational"
        ))
        keyboard.add(InlineKeyboardButton(
            text="💬 Задать вопрос боту",
            callback_data="llm:ask_questions"
        ))
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            response_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        await callback.answer("📞 Запрос отправлен менеджеру!")
        
    except Exception as e:
        logger.error("Error handling manager request", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка при отправке запроса")


@router.callback_query(F.data.startswith("lead:take:"))
async def handle_lead_take(callback: CallbackQuery, **kwargs):
    """Handle lead taking by manager."""
    try:
        lead_id = int(callback.data.split(":")[-1])
        manager_id = callback.from_user.id
        session = kwargs.get("session")

        admin_repo = AdminRepository(session)
        if not await admin_repo.can_take_leads(manager_id):
            await callback.answer("❌ У вас нет прав брать заявки", show_alert=True)
            return

        lead_service = LeadService(session)
        success, message = await lead_service.assign_lead(lead_id, manager_id)

        if success:
            lead = await lead_service.repository.get_lead_by_id(lead_id)
            user = None
            if lead:
                from app.repositories.user_repository import UserRepository
                user_repo = UserRepository(session)
                user = await user_repo.get_by_id(lead.user_id)

            manager_service = ManagerNotificationService(callback.bot, session)
            event_service = EventService(session)

            if lead and user:
                await manager_service.notify_lead_taken(lead, user, manager_id)
                await event_service.log_event(
                    user_id=user.id,
                    event_type="lead_taken",
                    payload={"lead_id": lead.id, "manager_id": manager_id},
                )

            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(
                text='♻️ Вернуть в очередь',
                callback_data=f'lead:return:{lead_id}',
            ))
            if lead:
                keyboard.add(InlineKeyboardButton(
                    text='👁 Профиль',
                    callback_data=f'lead:profile:{lead.user_id}',
                ))
            keyboard.adjust(1)

            manager_name = callback.from_user.full_name or callback.from_user.first_name or 'Менеджер'

            await callback.message.edit_text(
                f"✅ **Лид #{lead_id} взят в работу**\n\nМенеджер: {manager_name}\nID: {manager_id}",
                parse_mode='Markdown',
                reply_markup=keyboard.as_markup(),
            )

            await callback.answer('✅ Лид назначен на вас!')
        else:
            await callback.answer(f'❌ {message}', show_alert=True)

    except ValueError:
        await callback.answer('❌ Неверный ID лида', show_alert=True)
    except Exception as e:
        logger.error('Error taking lead', error=str(e), exc_info=True)
        await callback.answer('❌ Произошла ошибка', show_alert=True)


@router.callback_query(F.data.startswith("lead:return:"))
async def handle_lead_return(callback: CallbackQuery, **kwargs):
    """Return lead back to the manager queue."""
    try:
        lead_id = int(callback.data.split(":")[-1])
        manager_id = callback.from_user.id
        session = kwargs.get("session")

        admin_repo = AdminRepository(session)
        if not await admin_repo.can_take_leads(manager_id):
            await callback.answer("❌ У вас нет прав на это действие", show_alert=True)
            return

        lead_service = LeadService(session)
        success, message = await lead_service.return_lead_to_queue(lead_id, manager_id)

        if not success:
            await callback.answer(f'❌ {message}', show_alert=True)
            return

        from app.repositories.user_repository import UserRepository
        user_repo = UserRepository(session)
        lead = await lead_service.repository.get_lead_by_id(lead_id)
        user = await user_repo.get_by_id(lead.user_id) if lead else None

        manager_service = ManagerNotificationService(callback.bot, session)
        if lead and user:
            await manager_service.notify_new_lead(lead, user)

        await callback.message.edit_text(
            f'♻️ Лид #{lead_id} возвращён в очередь менеджером',
            parse_mode='Markdown'
        )

        if lead and user:
            event_service = EventService(session)
            await event_service.log_event(
                user_id=user.id,
                event_type='lead_returned',
                payload={"lead_id": lead.id, "manager_id": manager_id},
            )

        await callback.answer('Лид возвращён в очередь')

    except ValueError:
        await callback.answer('❌ Неверный ID лида', show_alert=True)
    except Exception as e:
        logger.error('Error returning lead', error=str(e), exc_info=True)
        await callback.answer('❌ Произошла ошибка', show_alert=True)


@router.callback_query(F.data.startswith("lead:profile:"))
async def handle_lead_profile(callback: CallbackQuery, **kwargs):
    """Show lead profile details."""
    try:
        user_id = int(callback.data.split(":")[-1])
        
        # Get user data
        from app.repositories.user_repository import UserRepository
        user_repo = UserRepository(kwargs.get("session"))
        user = await user_repo.get_by_id(user_id)
        
        if not user:
            await callback.answer("❌ Пользователь не найден")
            return
        
        # Get user's engagement data
        event_service = EventService(kwargs.get("session"))
        engagement_score = await event_service.get_engagement_score(user_id, hours=24)
        
        profile_text = f"""👤 **Профиль пользователя #{user_id}**

📋 **Основная информация:**
• **Имя:** {user.first_name or ''} {user.last_name or ''}
• **Username:** @{user.username if user.username else 'не указан'}
• **Телефон:** {user.phone if user.phone else 'не указан'}
• **Email:** {user.email if user.email else 'не указан'}

📊 **Сегментация:**
• **Сегмент:** {user.segment or 'не определен'}
• **Балл готовности:** {user.lead_score}/15
• **Этап воронки:** {user.funnel_stage}

📈 **Активность:**
• **Балл вовлеченности:** {engagement_score}
• **Заблокирован:** {'Да' if user.is_blocked else 'Нет'}
• **Источник:** {user.source or 'не указан'}

📅 **Даты:**
• **Регистрация:** {user.created_at.strftime('%d.%m.%Y %H:%M')}
• **Обновление:** {user.updated_at.strftime('%d.%m.%Y %H:%M')}"""
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="💬 Перехватить диалог",
            callback_data=f"manager:takeover:{user_id}"
        ))
        keyboard.add(InlineKeyboardButton(
            text="🎯 Создать лид",
            callback_data=f"lead:create:{user_id}"
        ))
        keyboard.add(InlineKeyboardButton(
            text="📱 История событий",
            callback_data=f"user:events:{user_id}"
        ))
        keyboard.add(InlineKeyboardButton(
            text="🔙 Закрыть",
            callback_data="close_message"
        ))
        keyboard.adjust(2, 1, 1)
        
        await callback.message.reply(
            profile_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        await callback.answer()
        
    except ValueError:
        await callback.answer("❌ Неверный ID пользователя")
    except Exception as e:
        logger.error("Error showing lead profile", error=str(e), exc_info=True)
        await callback.answer("❌ Произошла ошибка")


@router.callback_query(F.data.startswith("lead:create:"))
async def handle_manual_lead_creation(callback: CallbackQuery, **kwargs):
    """Handle manual lead creation by manager."""
    try:
        user_id = int(callback.data.split(":")[-1])
        
        # Get user
        from app.repositories.user_repository import UserRepository
        user_repo = UserRepository(kwargs.get("session"))
        user = await user_repo.get_by_id(user_id)
        
        if not user:
            await callback.answer("❌ Пользователь не найден")
            return
        
        # Create lead
        lead_service = LeadService(kwargs.get("session"))
        lead = await lead_service.create_lead_from_user(
            user=user,
            trigger_event="manual_creation",
            conversation_summary=f"Лид создан вручную менеджером {callback.from_user.first_name or 'Unknown'}"
        )
        
        # Notify in channel
        manager_service = ManagerNotificationService(callback.bot, kwargs.get("session"))
        await manager_service.notify_new_lead(lead, user)
        
        await callback.answer(f"✅ Лид #{lead.id} создан!")
        
    except ValueError:
        await callback.answer("❌ Неверный ID пользователя")
    except Exception as e:
        logger.error("Error creating manual lead", error=str(e), exc_info=True)
        await callback.answer("❌ Произошла ошибка")


@router.callback_query(F.data == "close_message")
async def handle_close_message(callback: CallbackQuery, **kwargs):
    """Close/delete message."""
    try:
        await callback.message.delete()
        await callback.answer()
    except Exception as e:
        logger.error("Error closing message", error=str(e), exc_info=True)
        await callback.answer()


def register_handlers(dp):
    """Register lead management handlers."""
    dp.include_router(router)