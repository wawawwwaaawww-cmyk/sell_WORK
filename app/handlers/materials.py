"""Material delivery handlers."""

import structlog
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import User, UserSegment
from app.services.materials_service import MaterialService
from app.services.event_service import EventService


router = Router()
logger = structlog.get_logger()


@router.callback_query(F.data == "materials:educational")
async def deliver_educational_materials(callback: CallbackQuery, user: User, **kwargs):
    """Deliver educational materials."""
    try:
        materials_service = MaterialService(kwargs.get("session"))
        materials = await materials_service.get_educational_materials(limit=5)
        
        # Format materials text
        materials_text = materials_service.format_materials_for_delivery(materials)
        
        # Add personalized intro
        intro_text = f"""🎓 **{user.first_name or 'Друг'}, держи образовательные материалы!**

Я подобрала для тебя основные материалы для изучения криптовалют:

{materials_text}

📚 *Рекомендую изучать материалы последовательно, начиная с основ.*

Готов к следующему шагу? 🚀"""
        
        # Create next steps keyboard
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="📞 Записаться на консультацию",
            callback_data="consult:offer"
        ))
        keyboard.add(InlineKeyboardButton(
            text="💬 Задать вопросы",
            callback_data="llm:ask_questions"
        ))
        keyboard.add(InlineKeyboardButton(
            text="👤 Связаться с менеджером",
            callback_data="manager:request"
        ))
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            intro_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        # Log event
        event_service = EventService(kwargs.get("session"))
        await event_service.log_event(
            user_id=user.id,
            event_type="materials_delivered",
            payload={
                "type": "educational",
                "count": len(materials)
            }
        )
        
        await callback.answer("📚 Материалы отправлены!")
        
    except Exception as e:
        logger.error("Error delivering educational materials", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка при загрузке материалов")


@router.callback_query(F.data.startswith("materials:segment:"))
async def deliver_segment_materials(callback: CallbackQuery, user: User, **kwargs):
    """Deliver materials for specific segment."""
    try:
        # Parse segment from callback data
        segment_name = callback.data.split(":")[-1]
        
        # Map segment name to enum
        segment_map = {
            "cold": UserSegment.COLD,
            "warm": UserSegment.WARM,
            "hot": UserSegment.HOT
        }
        
        segment = segment_map.get(segment_name, user.segment or UserSegment.COLD)
        
        materials_service = MaterialService(kwargs.get("session"))
        materials = await materials_service.get_materials_for_segment(
            segment=segment,
            funnel_stage=user.funnel_stage,
            limit=3
        )
        
        # Format materials text
        materials_text = materials_service.format_materials_for_delivery(materials)
        
        # Add segment-specific intro
        segment_intros = {
            UserSegment.COLD: "🌱 **Материалы для начинающих**",
            UserSegment.WARM: "📈 **Материалы для развития навыков**", 
            UserSegment.HOT: "🚀 **Экспертные материалы для продвинутых**"
        }
        
        intro = segment_intros.get(segment, "📚 **Полезные материалы**")
        
        full_text = f"""{intro}

{materials_text}

💡 *Эти материалы соответствуют твоему уровню подготовки и помогут достичь поставленных целей.*

Что дальше? 🤔"""
        
        # Create keyboard based on segment
        keyboard = InlineKeyboardBuilder()
        
        if segment == UserSegment.HOT:
            keyboard.add(InlineKeyboardButton(
                text="💳 Выбрать программу обучения",
                callback_data="offer:pay:advanced"
            ))
            keyboard.add(InlineKeyboardButton(
                text="📞 Консультация с экспертом",
                callback_data="consult:offer"
            ))
        else:
            keyboard.add(InlineKeyboardButton(
                text="📞 Записаться на консультацию",
                callback_data="consult:offer"
            ))
            keyboard.add(InlineKeyboardButton(
                text="💬 Обсудить программы",
                callback_data="llm:discuss_programs"
            ))
        
        keyboard.add(InlineKeyboardButton(
            text="📚 Другие материалы",
            callback_data="materials:browse"
        ))
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            full_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        # Log event
        event_service = EventService(kwargs.get("session"))
        await event_service.log_event(
            user_id=user.id,
            event_type="materials_delivered",
            payload={
                "type": "segment_specific",
                "segment": segment.value,
                "count": len(materials)
            }
        )
        
        await callback.answer(f"📚 Материалы для {segment.value}!")
        
    except Exception as e:
        logger.error("Error delivering segment materials", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка при загрузке материалов")


@router.callback_query(F.data == "materials:browse")
async def browse_materials(callback: CallbackQuery, user: User, **kwargs):
    """Show materials browser."""
    try:
        browse_text = """📚 **Каталог материалов**

Выбери интересующую тебя категорию:

🎓 **Обучающие материалы** - основы и теория
📈 **Кейсы успеха** - реальные истории учеников  
⭐ **Отзывы** - мнения о наших программах
🛡️ **Безопасность** - как защитить свои средства
📊 **Аналитика** - обзоры рынка и прогнозы

Что тебя интересует больше всего? 🤔"""
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="🎓 Обучающие материалы",
            callback_data="materials:category:educational"
        ))
        keyboard.add(InlineKeyboardButton(
            text="📈 Кейсы успеха",
            callback_data="materials:category:cases"
        ))
        keyboard.add(InlineKeyboardButton(
            text="⭐ Отзывы учеников",
            callback_data="materials:category:reviews"
        ))
        keyboard.add(InlineKeyboardButton(
            text="🛡️ Безопасность",
            callback_data="materials:category:security"
        ))
        keyboard.add(InlineKeyboardButton(
            text="📊 Рыночная аналитика",
            callback_data="materials:category:analytics"
        ))
        keyboard.add(InlineKeyboardButton(
            text="🔙 Назад",
            callback_data="back:main_menu"
        ))
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            browse_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        await callback.answer()
        
    except Exception as e:
        logger.error("Error showing materials browser", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка")


@router.callback_query(F.data.startswith("materials:category:"))
async def deliver_category_materials(callback: CallbackQuery, user: User, **kwargs):
    """Deliver materials by category."""
    try:
        category = callback.data.split(":")[-1]
        
        materials_service = MaterialService(kwargs.get("session"))
        
        # Get materials based on category
        if category == "educational":
            materials = await materials_service.get_educational_materials(limit=5)
            title = "🎓 **Обучающие материалы**"
        elif category == "cases":
            materials = await materials_service.get_case_studies(
                segment=user.segment or UserSegment.WARM,
                limit=4
            )
            title = "📈 **Кейсы успеха наших учеников**"
        elif category == "reviews":
            materials = await materials_service.get_reviews_and_testimonials(limit=4)
            title = "⭐ **Отзывы о наших программах**"
        elif category == "security":
            materials = await materials_service.get_materials_by_context(
                context="safety_concerns",
                segment=user.segment or UserSegment.COLD,
                limit=4
            )
            title = "🛡️ **Материалы по безопасности**"
        elif category == "analytics":
            materials = await materials_service.get_materials_by_context(
                context="market_analysis",
                segment=user.segment or UserSegment.WARM,
                limit=4
            )
            title = "📊 **Рыночная аналитика**"
        else:
            materials = []
            title = "📚 **Материалы**"
        
        # Format materials
        if materials:
            materials_text = materials_service.format_materials_for_delivery(materials)
            full_text = f"{title}\n\n{materials_text}"
        else:
            full_text = f"{title}\n\n📝 Материалы этой категории скоро появятся. А пока рекомендую записаться на консультацию с экспертом!"
        
        # Create keyboard
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="📞 Записаться на консультацию",
            callback_data="consult:offer"
        ))
        keyboard.add(InlineKeyboardButton(
            text="📚 Другие категории",
            callback_data="materials:browse"
        ))
        keyboard.add(InlineKeyboardButton(
            text="👤 Связаться с менеджером",
            callback_data="manager:request"
        ))
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            full_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="Markdown"
        )
        
        # Log event
        event_service = EventService(kwargs.get("session"))
        await event_service.log_event(
            user_id=user.id,
            event_type="materials_delivered",
            payload={
                "type": "category",
                "category": category,
                "count": len(materials)
            }
        )
        
        await callback.answer(f"📚 {title}")
        
    except Exception as e:
        logger.error("Error delivering category materials", error=str(e), user_id=user.id, exc_info=True)
        await callback.answer("Произошла ошибка при загрузке материалов")


def register_handlers(dp):
    """Register material handlers."""
    dp.include_router(router)