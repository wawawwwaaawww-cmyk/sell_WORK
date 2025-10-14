"""Full admin panel with production-ready functionality."""

import json
import logging
import re
from itertools import groupby
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import wraps
from html import escape
from typing import List, Optional, Dict, Any, Tuple
from collections import Counter
from zoneinfo import ZoneInfo

import structlog
from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import select, func
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from ..db import get_db
from ..models import (
    User,
    Payment,
    AdminRole,
    UserSegment,
    Product,
    Material,
    MaterialStatus,
    ABTest,
)
from ..repositories.admin_repository import AdminRepository
from ..repositories.system_settings_repository import SystemSettingsRepository
from ..repositories.product_repository import ProductRepository
from ..repositories.product_criteria_repository import ProductCriteriaRepository
from ..repositories.material_repository import MaterialRepository
from ..repositories.product_match_log_repository import ProductMatchLogRepository
from ..repositories.user_repository import UserRepository
from ..services.ab_testing_service import ABTestingService, VariantDefinition
from ..services.analytics_service import AnalyticsService
from ..services.analytics_formatter import (
    AB_STATUS_LABELS,
    clean_enum_value,
    format_percent,
    format_report_for_telegram,
    format_broadcast_metrics,
)
from ..services.bonus_content_manager import BonusContentManager
from ..services.scheduler_service import scheduler_service
from ..services.sentiment_service import sentiment_service
from ..services.survey_service import SurveyService
from ..services.product_matching_service import ProductMatchingService
from ..services.sendto_service import SendToService
from ..config import settings

logger = logging.getLogger(__name__)
seller_logger = structlog.get_logger("seller_krypto")
router = Router()
MOSCOW_TZ = ZoneInfo("Europe/Moscow")


class AdminStates(StatesGroup):
    """Admin FSM states."""
    # Consultation settings
    waiting_for_consultation_slots = State()
    waiting_for_cutoff_time = State()
    waiting_for_reminder_offset = State()

    # Broadcast states
    waiting_for_broadcast_content = State()
    waiting_for_broadcast_segment = State()
    waiting_for_broadcast_schedule = State()
    waiting_for_broadcast_confirmation = State()

    # A/B testing states
    waiting_for_ab_test_name = State()
    waiting_for_ab_test_variant_count = State()
    waiting_for_ab_test_variant_content = State()
    waiting_for_ab_test_variant_buttons = State()
    waiting_for_ab_test_confirmation = State()
    
    # Product states
    waiting_for_product_code = State()
    waiting_for_product_name = State()
    waiting_for_product_price = State()
    waiting_for_product_currency = State()
    waiting_for_product_short_desc = State()
    waiting_for_product_description = State()
    waiting_for_product_value_props = State()
    waiting_for_product_landing_url = State()
    waiting_for_product_edit_price = State()
    waiting_for_product_edit_description = State()
    waiting_for_product_edit_currency = State()
    waiting_for_product_edit_short_desc = State()
    waiting_for_product_edit_value_props = State()
    waiting_for_product_edit_landing = State()
    waiting_for_product_criteria = State()
    waiting_for_product_criteria_check_user = State()

    # Bonus management states
    waiting_for_bonus_file = State()
    waiting_for_bonus_description = State()

    # Sendto states
    waiting_for_sendto_recipients = State()
    waiting_for_sendto_content = State()




def admin_required(func):
    """Decorator to check if user is admin."""

    @wraps(func)
    async def wrapper(message_or_query, *args, **kwargs):
        user_id = message_or_query.from_user.id

        async for session in get_db():
            admin_repo = AdminRepository(session)
            is_admin = await admin_repo.is_admin(user_id)
            break

        if not is_admin:
            if isinstance(message_or_query, Message):
                await message_or_query.answer("❌ У вас нет прав администратора.")
            else:
                await message_or_query.answer("❌ У вас нет прав администратора.", show_alert=True)
            return

        return await func(message_or_query, *args, **kwargs)

    return wrapper


def role_required(required_role: AdminRole):
    """Decorator to check if admin has required role."""

    def decorator(func):
        @wraps(func)
        async def wrapper(message_or_query, *args, **kwargs):
            user_id = message_or_query.from_user.id

            async for session in get_db():
                admin_repo = AdminRepository(session)
                has_permission = await admin_repo.has_permission(user_id, required_role)
                break

            if not has_permission:
                if isinstance(message_or_query, Message):
                    await message_or_query.answer(f"❌ Требуется роль: {required_role.value}")
                else:
                    await message_or_query.answer(f"❌ Требуется роль: {required_role.value}", show_alert=True)
                return

            return await func(message_or_query, *args, **kwargs)

        return wrapper

    return decorator


def broadcast_permission_required(func):
    """Decorator ensuring admin can manage broadcasts/A/B tests."""

    @wraps(func)
    async def wrapper(message_or_query, *args, **kwargs):
        user_id = message_or_query.from_user.id

        async for session in get_db():
            admin_repo = AdminRepository(session)
            allowed = await admin_repo.can_manage_broadcasts(user_id)
            break

        if not allowed:
            response_text = "❌ Требуются права управления рассылками."
            if isinstance(message_or_query, Message):
                await message_or_query.answer(response_text)
            else:
                await message_or_query.answer(response_text, show_alert=True)
            return

        return await func(message_or_query, *args, **kwargs)

    return wrapper


MATERIAL_STATUS_LABELS = {
    MaterialStatus.READY.value: "🟢 Опубликован",
    MaterialStatus.DRAFT.value: "📝 Черновик",
    MaterialStatus.ARCHIVED.value: "⚪️ Архив",
}

SEGMENT_BADGES = {
    "cold": "❄️ Холодный",
    "warm": "🔥 Тёплый",
    "hot": "🚀 Горячий",
}

PRODUCT_STATUS_LABELS = {
    True: "🟢 Активен",
    False: "⚪️ Выключен",
}

AB_VARIANT_CODES = ["A", "B", "C"]
CANCEL_KEYWORDS = {"/cancel", "cancel", "стоп", "отмена", "stop", "выход"}


def _get_variant_code(index: int) -> str:
    """Return human-friendly variant label."""
    if 0 <= index < len(AB_VARIANT_CODES):
        return AB_VARIANT_CODES[index]
    return f"V{index + 1}"


def _summarize_text(text: str, limit: int = 140) -> str:
    """Trim text for preview."""
    clean = (text or "").strip()
    if len(clean) <= limit:
        return clean or "[без текста]"
    return clean[: limit - 1] + "…"


def _count_media_items(items: List[Dict[str, Any]]) -> int:
    """Count non-text items in content list."""
    return sum(1 for item in items if item.get("type") != "text")


def _summarize_variant_entry(entry: Dict[str, Any]) -> str:
    """Create preview snippet for variant."""
    snippet = _summarize_text(entry.get("body") or "")
    media_count = _count_media_items(entry.get("content") or [])
    if media_count:
        snippet += f" (+{media_count} влож.)"
    return snippet


def _is_cancel_text(text: Optional[str]) -> bool:
    """Check if user input means cancellation."""
    if not text:
        return False
    return text.strip().lower() in CANCEL_KEYWORDS


def _parse_cta_buttons(raw: str) -> List[Dict[str, str]]:
    """Parse CTA button definitions from user input."""
    buttons: List[Dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            raise ValueError("Каждая кнопка должна быть в формате «Текст | действие».")
        text_part, action_part = [part.strip() for part in line.split("|", 1)]
        if not text_part or not action_part:
            raise ValueError("Нужно указать и текст, и действие для кнопки.")

        action_lower = action_part.lower()
        if action_lower.startswith("url:"):
            url_value = action_part.split(":", 1)[1].strip()
            buttons.append({"text": text_part, "url": url_value})
        elif action_lower.startswith("http://") or action_lower.startswith("https://"):
            buttons.append({"text": text_part, "url": action_part})
        else:
            if action_lower.startswith("callback:"):
                action_part = action_part.split(":", 1)[1].strip()
            buttons.append({"text": text_part, "callback_data": action_part})

    return buttons


def _extract_body_from_items(items: List[Dict[str, Any]], fallback: str) -> str:
    """Extract primary text body from content items."""
    for item in items:
        if item.get("type") == "text":
            return item.get("plain_text") or item.get("text") or fallback or ""
    return fallback or "[без текста]"


def _build_ab_test_preview_text(name: str, variants: List[Dict[str, Any]]) -> str:
    """Render preview text for confirmation step."""
    lines = [
        "🧪 <b>Предпросмотр A/B теста</b>",
        f"Название: {escape(name)}",
        f"Вариантов: {len(variants)}",
        "",
    ]

    for index, variant in enumerate(variants):
        code = variant.get("code") or _get_variant_code(index)
        lines.append(f"{code}) {_summarize_variant_entry(variant)}")
        buttons = variant.get("buttons") or []
        if buttons:
            lines.append("   CTA: " + ", ".join(btn.get("text") for btn in buttons))
        lines.append("")

    lines.append("📤 Рассылка будет отправлена 30% активной аудитории (равномерно по вариантам).")
    return "\n".join(lines)


def _coerce_datetime(value: Optional[Any]) -> Optional[datetime]:
    """Convert ISO string to datetime if needed."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _build_ab_test_result_text(analysis: Dict[str, Any]) -> str:
    """Format detailed statistics for an A/B test."""
    name = analysis.get("name") or "A/B тест"
    status_value = analysis.get("status", "unknown")
    status_label = AB_STATUS_LABELS.get(clean_enum_value(status_value), status_value)
    started_at = _format_datetime(_coerce_datetime(analysis.get("started_at")))
    finished_at = _format_datetime(_coerce_datetime(analysis.get("finished_at")))
    audience = analysis.get("audience_size") or 0
    test_size = analysis.get("test_size") or 0

    lines = [
        f"🧪 <b>{escape(name)}</b>",
        f"Статус: {status_label}",
        f"Старт: {started_at}",
        f"Завершение: {finished_at}",
        f"Охват теста: {test_size} из {audience}",
        "",
        "Показатели по вариантам:",
    ]

    for variant in analysis.get("variants", []):
        lines.append(
            f"• {variant.get('variant')}: доставлено {variant.get('delivered', 0)}, "
            f"клики {variant.get('unique_clicks', 0)}, CTR {format_percent(variant.get('ctr'))}, "
            f"лиды {variant.get('leads', 0)} (CR {format_percent(variant.get('cr'))}), "
            f"отписки {variant.get('unsubscribed', 0)} ({format_percent(variant.get('unsub_rate'))}), "
            f"блокировки {variant.get('blocked', 0)}"
        )

    winner = analysis.get("winner")
    lines.append("")
    if winner:
        lines.append(
            f"🏆 Победитель: вариант {winner.get('variant')} "
            f"(CTR {format_percent(winner.get('ctr'))}, CR {format_percent(winner.get('cr'))})"
        )
    else:
        lines.append("🏳️ Победитель не определён.")

    return "\n".join(lines)


def _extract_broadcast_items(message: Message) -> List[Dict[str, Any]]:
    """Convert an incoming admin message into broadcast content items."""

    seller_logger.info(
        "broadcast.extract.start",
        message_id=message.message_id,
        from_user=getattr(message.from_user, "id", None),
        content_type=message.content_type,
        media_group_id=message.media_group_id,
    )

    items: List[Dict[str, Any]] = []

    plain_text = message.text or ""
    text_html = getattr(message, "html_text", None)

    if plain_text:
        items.append(
            {
                "type": "text",
                "text": text_html or plain_text,
                "plain_text": plain_text,
                "parse_mode": "HTML" if text_html else None,
            }
        )
        seller_logger.info(
            "broadcast.extract.item",
            message_id=message.message_id,
            item_type="text",
            length=len(text_html or plain_text),
        )

    caption_plain = message.caption or ""
    caption_html = getattr(message, "html_caption", None)
    parse_mode = "HTML" if caption_html else None
    caption_text = caption_html or caption_plain or None

    if message.photo:
        file_id = message.photo[-1].file_id
        items.append(
            {
                "type": "photo",
                "file_id": file_id,
                "caption": caption_text,
                "plain_caption": caption_plain or None,
                "parse_mode": parse_mode,
            }
        )
        seller_logger.info(
            "broadcast.extract.item",
            message_id=message.message_id,
            item_type="photo",
            file_id=file_id,
        )

    if message.video:
        file_id = message.video.file_id
        items.append(
            {
                "type": "video",
                "file_id": file_id,
                "caption": caption_text,
                "plain_caption": caption_plain or None,
                "parse_mode": parse_mode,
            }
        )
        seller_logger.info(
            "broadcast.extract.item",
            message_id=message.message_id,
            item_type="video",
            file_id=file_id,
        )

    if message.document:
        file_id = message.document.file_id
        items.append(
            {
                "type": "document",
                "file_id": file_id,
                "caption": caption_text,
                "plain_caption": caption_plain or None,
                "parse_mode": parse_mode,
                "file_name": message.document.file_name,
            }
        )
        seller_logger.info(
            "broadcast.extract.item",
            message_id=message.message_id,
            item_type="document",
            file_id=file_id,
        )

    if message.audio:
        file_id = message.audio.file_id
        items.append(
            {
                "type": "audio",
                "file_id": file_id,
                "caption": caption_text,
                "plain_caption": caption_plain or None,
                "parse_mode": parse_mode,
            }
        )
        seller_logger.info(
            "broadcast.extract.item",
            message_id=message.message_id,
            item_type="audio",
            file_id=file_id,
        )

    if message.voice:
        file_id = message.voice.file_id
        items.append(
            {
                "type": "voice",
                "file_id": file_id,
            }
        )
        seller_logger.info(
            "broadcast.extract.item",
            message_id=message.message_id,
            item_type="voice",
            file_id=file_id,
        )

    if not items:
        seller_logger.warning(
            "broadcast.extract.empty",
            message_id=message.message_id,
            content_type=message.content_type,
        )
        raise ValueError("Unsupported message type for broadcast")

    seller_logger.info(
        "broadcast.extract.complete",
        message_id=message.message_id,
        total_items=len(items),
    )
    return items


async def _send_preview_items(bot, chat_id: int, items: List[Dict[str, Any]]) -> None:
    """Send broadcast items to a chat for preview purposes."""

    seller_logger.info(
        "broadcast.preview.send_start",
        chat_id=chat_id,
        total_items=len(items),
    )

    for index, item in enumerate(items):
        item_type = item.get("type")
        try:
            if item_type == "text":
                await bot.send_message(
                    chat_id=chat_id,
                    text=item.get("text", ""),
                    parse_mode=item.get("parse_mode"),
                )
            elif item_type == "photo":
                kwargs = {
                    "chat_id": chat_id,
                    "photo": item.get("file_id"),
                }
                if item.get("caption"):
                    kwargs["caption"] = item["caption"]
                    kwargs["parse_mode"] = item.get("parse_mode")
                await bot.send_photo(**kwargs)
            elif item_type == "video":
                kwargs = {
                    "chat_id": chat_id,
                    "video": item.get("file_id"),
                }
                if item.get("caption"):
                    kwargs["caption"] = item["caption"]
                    kwargs["parse_mode"] = item.get("parse_mode")
                await bot.send_video(**kwargs)
            elif item_type == "document":
                kwargs = {
                    "chat_id": chat_id,
                    "document": item.get("file_id"),
                }
                if item.get("caption"):
                    kwargs["caption"] = item["caption"]
                    kwargs["parse_mode"] = item.get("parse_mode")
                await bot.send_document(**kwargs)
            elif item_type == "audio":
                kwargs = {
                    "chat_id": chat_id,
                    "audio": item.get("file_id"),
                }
                if item.get("caption"):
                    kwargs["caption"] = item["caption"]
                    kwargs["parse_mode"] = item.get("parse_mode")
                await bot.send_audio(**kwargs)
            elif item_type == "voice":
                await bot.send_voice(
                    chat_id=chat_id,
                    voice=item.get("file_id"),
                )
            else:
                seller_logger.warning(
                    "broadcast.preview.unsupported_item",
                    chat_id=chat_id,
                    index=index,
                    item_type=item_type,
                )
                continue

            seller_logger.info(
                "broadcast.preview.item_sent",
                chat_id=chat_id,
                index=index,
                item_type=item_type,
            )

        except Exception as exc:
            seller_logger.error(
                "broadcast.preview.error",
                chat_id=chat_id,
                index=index,
                item_type=item_type,
                error=str(exc),
            )
            raise

    seller_logger.info(
        "broadcast.preview.send_complete",
        chat_id=chat_id,
        total_items=len(items),
    )

def _format_currency(amount: Decimal) -> str:
    try:
        return f"{amount:,.0f}".replace(",", " ") + " ₽"
    except Exception:  # pragma: no cover - fallback
        return f"{amount} ₽"


def _format_datetime(value: Optional[datetime]) -> str:
    if not value:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def _shorten(text: Optional[str], limit: int = 400) -> str:
    if not text:
        return "—"
    clean = text.strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


async def _get_materials_for_admin(session, limit: int = 10) -> List[Material]:
    stmt = (
        select(Material)
        .options(
            selectinload(Material.versions),
            selectinload(Material.tags_rel),
            selectinload(Material.segments_rel),
        )
        .order_by(Material.updated_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.scalars().unique().all()


async def _get_material_by_id(session, material_id: str) -> Optional[Material]:
    stmt = (
        select(Material)
        .options(
            selectinload(Material.versions),
            selectinload(Material.tags_rel),
            selectinload(Material.segments_rel),
        )
        .where(Material.id == material_id)
    )
    result = await session.execute(stmt)
    return result.scalars().unique().one_or_none()


def _material_badge(material: Material) -> str:
    return MATERIAL_STATUS_LABELS.get(material.status, material.status)


def _material_segments(material: Material) -> str:
    segments = sorted({(seg.segment or "").lower() for seg in material.segments_rel if seg.segment})
    if not segments:
        return "—"
    return ", ".join(SEGMENT_BADGES.get(seg, seg) for seg in segments)


def _material_tags(material: Material) -> str:
    tags = [tag.tag for tag in material.tags_rel if tag.tag]
    if not tags:
        return "—"
    preview = ", ".join(tags[:6])
    if len(tags) > 6:
        preview += "…"
    return preview


def _material_primary_url(material: Material) -> Optional[str]:
    version = material.active_version
    if version and version.primary_asset_url:
        return version.primary_asset_url
    return None


def _build_material_detail(material: Material) -> Tuple[str, InlineKeyboardMarkup]:
    status_label = _material_badge(material)
    segments = _material_segments(material)
    tags = _material_tags(material)
    summary = escape(_shorten(material.summary or (material.active_version.extracted_text if material.active_version else ""), 600))
    category = material.category or "—"
    priority = material.priority if getattr(material, "priority", None) is not None else 0
    updated = _format_datetime(material.updated_at)
    slug = escape(material.slug)
    language = material.language if getattr(material, "language", None) else "ru"
    versions_count = len(material.versions) if material.versions else 0

    primary_url = _material_primary_url(material)

    text = (
        f"📚 <b>{escape(material.title)}</b>\n"
        f"ID: <code>{material.id}</code>\n"
        f"Слаг: <code>{slug}</code>\n"
        f"Статус: {status_label}\n"
        f"Категория: {category or '—'}\n"
        f"Приоритет: {priority}\n"
        f"Язык: {language}\n"
        f"Сегменты: {segments}\n"
        f"Теги: {tags}\n"
        f"Версий: {versions_count}\n"
        f"Обновлён: {updated}\n\n"
        f"<b>Аннотация</b>\n{summary or '—'}"
    )

    builder = InlineKeyboardBuilder()
    if primary_url:
        builder.add(InlineKeyboardButton(text="🌐 Открыть материал", url=primary_url))

    if material.status == MaterialStatus.READY.value:
        target = MaterialStatus.ARCHIVED.value
        toggle_text = "🛑 Архивировать"
    else:
        target = MaterialStatus.READY.value
        toggle_text = "✅ Опубликовать"

    builder.add(
        InlineKeyboardButton(
            text=toggle_text,
            callback_data=f"material_toggle:{material.id}:{target}"
        )
    )
    builder.row(InlineKeyboardButton(text="⬅️ К списку", callback_data="material_list"))
    builder.row(InlineKeyboardButton(text="📚 Раздел", callback_data="admin_materials"))
    return text, builder.as_markup()


async def _get_product_by_id(session, product_id: int) -> Optional[Product]:
    stmt = (
        select(Product)
        .options(selectinload(Product.criteria))
        .where(Product.id == product_id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def _normalize_markdown(text: str) -> str:
    """Remove markdown symbols for admin previews."""
    if not text:
        return ""
    # Basic removal of emphasis markers
    cleaned = re.sub(r"[*_`]+", "", text)
    return cleaned.strip()


def _build_survey_catalog(survey_service) -> Dict[int, Dict[str, Any]]:
    """Return catalog of survey questions with answer indices."""
    catalog: Dict[int, Dict[str, Any]] = {}
    for idx, (code, question) in enumerate(survey_service.questions.items(), start=1):
        answers = []
        for answer_idx, (answer_code, option) in enumerate(question.get("options", {}).items(), start=1):
            answers.append(
                {
                    "id": answer_idx,
                    "question_code": code,
                    "code": answer_code,
                    "text": _normalize_markdown(option.get("text", "")),
                }
            )
        catalog[idx] = {
            "code": code,
            "text": _normalize_markdown(question.get("text", "")),
            "answers": answers,
        }
    return catalog


def _format_survey_reference(catalog: Dict[int, Dict[str, Any]]) -> str:
    """Format survey catalog for admin display."""
    lines: list[str] = []
    for q_idx in sorted(catalog):
        entry = catalog[q_idx]
        lines.append(f"Q{q_idx}. {entry['text']}")
        for answer in entry["answers"]:
            lines.append(f"  {answer['id']}) {answer['text']} ({answer['code']})")
        lines.append("")
    return "\n".join(lines).strip()


_CRITERIA_ENTRY_SPLIT = re.compile(r"[;\n]+")
_QUESTION_HEADER = re.compile(r"^\s*(?:Q)?(?P<question>\d+)\s*:\s*(?P<body>.+)$", re.IGNORECASE)
_GROUP_WEIGHT = re.compile(r"\(\s*(?:вес|weight|w)\s*=?\s*(?P<weight>[-+]?\d+)\s*\)", re.IGNORECASE)
_INLINE_NOTE = re.compile(r"(?:note|коммент|причина)\s*[:=]\s*(?P<note>.+)", re.IGNORECASE)


def _parse_criteria_input(raw: str, catalog: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse admin input into structured criteria."""
    entries: List[Dict[str, Any]] = []
    errors: List[str] = []

    segments = [segment.strip() for segment in _CRITERIA_ENTRY_SPLIT.split(raw or "") if segment.strip()]
    if not segments:
        raise ValueError("Не найдено ни одного критерия. Используйте формат 'Q1: 2,4'.")

    for segment in segments:
        match = _QUESTION_HEADER.match(segment)
        if not match:
            errors.append(f"Не могу распознать строку: {segment}")
            continue

        question_id = int(match.group("question"))
        body = match.group("body").strip()
        catalog_entry = catalog.get(question_id)
        if not catalog_entry:
            errors.append(f"Вопрос Q{question_id} не найден в анкете.")
            continue

        group_weight: Optional[int] = None
        # Extract group-level weight if present
        group_match = _GROUP_WEIGHT.search(body)
        if group_match:
            group_weight = int(group_match.group("weight"))
            body = _GROUP_WEIGHT.sub("", body).strip()

        tokens = [token.strip() for token in body.split(",") if token.strip()]
        if not tokens:
            errors.append(f"Для Q{question_id} не указаны ответы.")
            continue

        for token in tokens:
            answer_weight = group_weight if group_weight is not None else 1
            note: Optional[str] = None
            inner = None

            # Extract inline data in parentheses
            if "(" in token and token.endswith(")"):
                token_body, inner_body = token.split("(", 1)
                inner = inner_body[:-1]  # drop closing )
                token = token_body.strip()
            elif "[" in token and token.endswith("]"):
                token_body, inner_body = token.split("[", 1)
                inner = inner_body[:-1]
                token = token_body.strip()

            if not token.isdigit():
                errors.append(f"Ответ '{token}' должен быть числом. См. Q{question_id}.")
                continue

            answer_id = int(token)
            answers = catalog_entry["answers"]
            if answer_id < 1 or answer_id > len(answers):
                errors.append(f"Ответ {answer_id} отсутствует в Q{question_id}.")
                continue

            if inner:
                parts = [part.strip() for part in re.split(r"[|;]", inner) if part.strip()]
                for part in parts:
                    if _GROUP_WEIGHT.match(f"(вес {part})"):
                        answer_weight = int(part)
                        continue
                    if re.fullmatch(r"[-+]?\d+", part):
                        answer_weight = int(part)
                        continue
                    inline_note = _INLINE_NOTE.search(part)
                    if inline_note:
                        note = inline_note.group("note")
                        continue
                    if part.lower().startswith("вес"):
                        digits = re.findall(r"[-+]?\d+", part)
                        if digits:
                            answer_weight = int(digits[0])
                        continue
                    note = part.strip("\"' ")

            answer_entry = answers[answer_id - 1]
            entries.append(
                {
                    "question_id": question_id,
                    "question_code": answer_entry.get("question_code") or catalog_entry["code"],
                    "answer_id": answer_id,
                    "answer_code": answer_entry["code"],
                    "weight": answer_weight,
                    "note": note,
                }
            )

    if errors:
        raise ValueError("\n".join(errors))

    return entries


def _format_criteria_table(criteria: List) -> str:
    """Pretty-print criteria grouped by question."""
    if not criteria:
        return "Критерии не заданы."

    lines: list[str] = []
    sorted_criteria = sorted(criteria, key=lambda item: (item.question_id, item.answer_id))
    for question_id, group in groupby(sorted_criteria, key=lambda item: item.question_id):
        entries = list(group)
        line_parts = []
        for item in entries:
            note = f" ({item.note})" if item.note else ""
            line_parts.append(f"A{item.answer_id}[{item.weight:+d}]{note}")
        lines.append(f"Q{question_id}: " + ", ".join(line_parts))
    return "\n".join(lines)


def _build_product_detail(product: Product) -> Tuple[str, InlineKeyboardMarkup]:
    status_label = PRODUCT_STATUS_LABELS.get(product.is_active, "—")
    price_display = _format_currency(product.price)
    currency = escape(product.currency or "RUB")
    price_text = f"{price_display} {currency}"
    slug = escape(product.slug) if product.slug else "—"
    short_desc = escape(_shorten(product.short_desc, 240)) if product.short_desc else "—"
    description = escape(_shorten(product.description, 500)) if product.description else "—"
    landing_url = product.landing_url or product.payment_landing_url
    payment_url = product.payment_landing_url
    value_props = product.value_props or []
    if isinstance(value_props, str):
        try:
            value_props = json.loads(value_props)
        except json.JSONDecodeError:
            value_props = [value_props]
    if not isinstance(value_props, list):
        value_props = [str(value_props)]
    value_props_lines = "\n".join(f"• {escape(str(item))}" for item in value_props[:5]) or "—"

    criteria_summary = "Критерии не настроены"
    criteria_lines: list[str] = []
    if product.criteria:
        positives = sum(1 for c in product.criteria if c.weight >= 0)
        negatives = sum(1 for c in product.criteria if c.weight < 0)
        criteria_summary = f"{len(product.criteria)} правил · +{positives} / −{negatives}"
        for criterion in product.criteria[:8]:
            note = f" ({escape(criterion.note)})" if criterion.note else ""
            criteria_lines.append(
                f"Q{criterion.question_id} → A{criterion.answer_id} [{criterion.weight:+d}]{note}"
            )
        if len(product.criteria) > 8:
            criteria_lines.append("…")
    criteria_details = "\n".join(criteria_lines) if criteria_lines else ""
    preview_props = [escape(str(item)) for item in value_props[:2]]
    preview_block = "\n".join(f"• {item}" for item in preview_props) if preview_props else "• Добавьте ключевые выгоды"

    meta_json = "—"
    if product.meta:
        try:
            meta_json = json.dumps(product.meta, ensure_ascii=False, indent=2)
            if len(meta_json) > 600:
                meta_json = meta_json[:600].rstrip() + "…"
            meta_json = escape(meta_json)
        except Exception:  # pragma: no cover
            meta_json = escape(str(product.meta))

    text = (
        f"💰 <b>{escape(product.name)}</b>\n"
        f"ID: <code>{product.id}</code>\n"
        f"Код: <code>{escape(product.code)}</code>\n"
        f"Slug: {slug}\n"
        f"Статус: {status_label}\n"
        f"Цена: {price_text}\n"
        f"Лендинг: {landing_url or '—'}\n"
        f"Оплата: {payment_url or '—'}\n"
        f"\n<b>Коротко</b>\n{short_desc}\n"
        f"\n<b>Ключевые выгоды</b>\n{value_props_lines}\n"
        f"\n<b>Предпросмотр для клиента</b>\n"
        f"{escape(product.name)} — {price_text}\n"
        f"{preview_block}\n"
        "Кнопка: «Хочу программу»\n"
        f"\n<b>Описание</b>\n{description}\n"
        f"\n<b>Критерии подбора</b>\n{criteria_summary}\n"
        f"{criteria_details}\n"
        f"\n<b>Meta</b>\n<pre>{meta_json}</pre>"
    )

    builder = InlineKeyboardBuilder()
    if landing_url:
        builder.add(InlineKeyboardButton(text="🌐 Ленд", url=landing_url))
    if payment_url and payment_url != landing_url:
        builder.add(InlineKeyboardButton(text="💳 Оплата", url=payment_url))

    builder.add(
        InlineKeyboardButton(
            text="🔁 Переключить статус",
            callback_data=f"product_toggle:{product.id}"
        )
    )
    builder.row(
        InlineKeyboardButton(text="💱 Валюта", callback_data=f"product_edit_currency:{product.id}"),
        InlineKeyboardButton(text="🪪 Коротко", callback_data=f"product_edit_short:{product.id}"),
    )
    builder.row(
        InlineKeyboardButton(text="✏️ Изменить цену", callback_data=f"product_edit_price:{product.id}"),
        InlineKeyboardButton(text="📝 Описание", callback_data=f"product_edit_description:{product.id}"),
    )
    builder.row(
        InlineKeyboardButton(text="🎯 Value props", callback_data=f"product_edit_value:{product.id}"),
        InlineKeyboardButton(text="🔗 Лендинг", callback_data=f"product_edit_landing:{product.id}"),
    )
    builder.row(
        InlineKeyboardButton(text="🧠 Настроить критерии", callback_data=f"product_criteria:{product.id}"),
        InlineKeyboardButton(text="🧪 Проверить пользователя", callback_data=f"product_match_check:{product.id}"),
    )
    builder.row(InlineKeyboardButton(text="⬅️ К списку", callback_data="product_list"))
    builder.row(InlineKeyboardButton(text="💰 Раздел", callback_data="admin_products"))
    return text, builder.as_markup()
@router.message(Command("admin"))
@admin_required
async def admin_panel(message: Message):
    """Show full admin panel."""
    async for session in get_db():
        admin_repo = AdminRepository(session)
        capabilities = await admin_repo.get_admin_capabilities(message.from_user.id)
        break
        
    buttons = []
    
    # Analytics (all admins)
    buttons.append([InlineKeyboardButton(text="📊 Аналитика", callback_data="admin_analytics")])

    # A/B testing overview (all admins)
    buttons.append([InlineKeyboardButton(text="🧪 A/B тесты", callback_data="admin_abtests")])

    # Leads management (all admins)
    buttons.append([InlineKeyboardButton(text="👥 Лиды", callback_data="admin_leads")])
    
    # Broadcast management (editors and above)
    if capabilities.get("can_manage_broadcasts"):
        buttons.append([InlineKeyboardButton(text="📢 Рассылки", callback_data="admin_broadcasts")])
        buttons.append([InlineKeyboardButton(text="🎁 Бонус", callback_data="admin_bonus")])

    # Materials management (editors and above)
    if capabilities.get("can_manage_materials"):
        buttons.append([InlineKeyboardButton(text="📚 Материалы", callback_data="admin_materials")])

    # User management (admins and above)
    if capabilities.get("can_manage_users"):
        buttons.append([InlineKeyboardButton(text="👤 Пользователи", callback_data="admin_users")])

    # Payment management (admins and above)
    if capabilities.get("can_manage_payments"):
        buttons.append([InlineKeyboardButton(text="💳 Платежи", callback_data="admin_payments")])

    # Product management (admins and above)
    if capabilities.get("can_manage_products"):
        buttons.append([InlineKeyboardButton(text="💰 Продукты", callback_data="admin_products")])
    
    # Admin management (owners only)
    if capabilities.get("can_manage_admins"):
        buttons.append([InlineKeyboardButton(text="⚙️ Админы", callback_data="admin_admins")])

    buttons.append([InlineKeyboardButton(text="⚙️ Системные настройки", callback_data="admin_settings")])
    buttons.append([InlineKeyboardButton(text="📅 Настройки консультаций", callback_data="admin_consult_settings")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    role = capabilities.get("role", "unknown")
    
    await message.answer(
        f"🔧 <b>Панель администратора</b>\n\n"
        f"👤 Ваша роль: <b>{role}</b>\n\n"
        "Выберите нужный раздел:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def _render_settings_panel(callback: CallbackQuery, session) -> None:
    """Render settings overview with sentiment toggle."""
    local_session = None
    target_session = session
    if target_session is None:
        async for db_session in get_db():
            local_session = db_session
            target_session = db_session
            break

    repo = SystemSettingsRepository(target_session)
    enabled = await repo.get_value(sentiment_service.AUTO_SETTING_KEY, default=True)
    enabled_bool = bool(enabled)
    toggle_text = "🛑 Выключить авто-оценку" if enabled_bool else "🟢 Включить авто-оценку"
    status_text = "включена" if enabled_bool else "выключена"

    lines = [
        "⚙️ <b>Системные настройки</b>",
        "",
        f"🤖 Авто-оценка сообщений: <b>{status_text}</b>",
        "",
        "При выключении новые сообщения не будут отправляться в LLM — "
        "метки фиксируются как нейтральные.",
    ]

    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text=toggle_text, callback_data="settings:sentiment_toggle"))
    keyboard.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back"))
    keyboard.adjust(1)

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=keyboard.as_markup(),
        parse_mode="HTML",
    )

    if local_session is not None:
        await local_session.commit()
        await local_session.close()


@router.callback_query(F.data == "admin_settings")
@admin_required
async def admin_settings_menu(callback: CallbackQuery, **kwargs):
    """Show system settings panel."""
    session = kwargs.get("session")
    await _render_settings_panel(callback, session)
    await callback.answer()


@router.callback_query(F.data == "settings:sentiment_toggle")
@admin_required
async def admin_toggle_sentiment(callback: CallbackQuery, **kwargs):
    """Toggle automatic sentiment classification."""
    session = kwargs.get("session")
    current = await sentiment_service.is_auto_enabled()
    new_state = not current
    await sentiment_service.set_auto_enabled(new_state)
    await _render_settings_panel(callback, session)
    await callback.answer("Настройка обновлена")


@router.message(Command("dashboard"))
@role_required(AdminRole.MANAGER)
async def manager_dashboard(message: Message):
    """Provide quick analytics dashboard for managers."""
    try:
        report = {}
        async for session in get_db():
            service = AnalyticsService(session)
            report = await service.get_comprehensive_report()
            break

        if not report:
            await message.answer("❌ Не удалось получить аналитику. Попробуйте позже.")
            return

        stats_text = format_report_for_telegram(report)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_analytics")],
            [InlineKeyboardButton(text="🧪 A/B тесты", callback_data="admin_abtests")],
            [InlineKeyboardButton(text="📢 Рассылки", callback_data="manager_broadcasts")],
        ])

        await message.answer(stats_text, reply_markup=keyboard, parse_mode="HTML")

    except Exception:
        logger.exception("Error showing manager dashboard")
        await message.answer("❌ Ошибка при загрузке дашборда. Сообщите администратору.")


# Analytics
@router.callback_query(F.data == "admin_analytics")
@admin_required
async def show_analytics(callback: CallbackQuery):
    """Show comprehensive analytics."""
    try:
        report = {}
        async for session in get_db():
            service = AnalyticsService(session)
            report = await service.get_comprehensive_report()
            break

        if not report:
            await callback.answer("❌ Не удалось получить аналитику", show_alert=True)
            return

        stats_text = format_report_for_telegram(report)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_analytics")],
            [InlineKeyboardButton(text="🧪 A/B тесты", callback_data="admin_abtests")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
        ])

        await callback.message.edit_text(stats_text, reply_markup=keyboard, parse_mode="HTML")

    except Exception:
        logger.exception("Error showing analytics")
        await callback.answer("❌ Ошибка при загрузке аналитики", show_alert=True)


@router.callback_query(F.data == "admin_abtests")
@admin_required
async def show_abtests(callback: CallbackQuery):
    """Show A/B testing hub with quick stats and actions."""
    try:
        ab_report: Dict[str, Any] = {}
        can_create = False
        async for session in get_db():
            service = AnalyticsService(session)
            ab_report = await service.get_ab_test_metrics()
            admin_repo = AdminRepository(session)
            can_create = await admin_repo.can_manage_broadcasts(callback.from_user.id)
            break

        if ab_report.get("error") == "ab_tables_missing":
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]]
            )
            await callback.message.edit_text(
                "🧪 A/B тесты недоступны. Требуется выполнить миграции БД (например, <code>alembic upgrade head</code>).",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            await callback.answer()
            return

        if ab_report.get("error") == "ab_query_failed":
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]]
            )
            await callback.message.edit_text(
                "⚠️ Не удалось получить данные A/B тестов. Проверьте миграции и повторите позже.",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            await callback.answer()
            return

        summary = ab_report.get("summary") or {}
        tests = ab_report.get("tests") or []

        lines = [
            "🧪 <b>A/B тесты</b>",
            f"Всего: {summary.get('total', 0)} | Активные: {summary.get('running', 0)} | Завершённые: {summary.get('completed', 0)}",
        ]

        if tests:
            lines.append("")
            lines.append("Последние тесты:")
            for test in tests[:3]:
                status_value = test.get("status", "unknown")
                status_label = AB_STATUS_LABELS.get(
                    clean_enum_value(status_value),
                    status_value,
                )
                total_delivered = sum(variant.get("delivered", 0) for variant in test.get("variants", []))
                lines.append(
                    f"• #{test.get('id')} {test.get('name', 'Без названия')} — {status_label}, доставлено {total_delivered}"
                )
        else:
            lines.append("")
            lines.append("📭 Тесты еще не запускались.")

        lines.append("")
        lines.append("Выберите действие:")

        text = "\n".join(lines)

        keyboard_rows = []
        if can_create:
            keyboard_rows.append([InlineKeyboardButton(text="➕ Создать тест", callback_data="admin_abtests_create")])
        keyboard_rows.append([InlineKeyboardButton(text="📊 Результаты", callback_data="admin_abtests_results")])
        keyboard_rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_abtests")])
        keyboard_rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")])

        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer()

    except Exception:
        logger.exception("Error showing A/B tests")
        await callback.answer("❌ Ошибка при загрузке A/B тестов", show_alert=True)


@router.callback_query(F.data == "admin_abtests_create")
@broadcast_permission_required
async def admin_abtests_create(callback: CallbackQuery, state: FSMContext):
    """Start A/B test creation wizard."""
    try:
        await state.clear()
        await state.update_data(
            ab_test={
                "variants": [],
                "current_index": 0,
                "total_variants": 0,
            }
        )
        await state.set_state(AdminStates.waiting_for_ab_test_name)

        await callback.message.edit_text(
            "🧪 <b>Создание A/B теста</b>\n\n"
            "Введите название теста (для отчётов и админки).",
            parse_mode="HTML",
        )
        await callback.answer()
    except Exception:
        logger.exception("Error initializing A/B test wizard")
        await callback.answer("❌ Не удалось начать создание теста", show_alert=True)


@router.message(AdminStates.waiting_for_ab_test_name)
@broadcast_permission_required
async def admin_abtests_set_name(message: Message, state: FSMContext):
    """Handle A/B test name input."""
    if _is_cancel_text(message.text):
        await state.clear()
        await message.answer("❌ Создание теста отменено.")
        return

    name = (message.text or "").strip()
    if len(name) < 3:
        await message.answer("Название должно содержать минимум 3 символа. Попробуйте снова.")
        return

    data = await state.get_data()
    ab_data = data.get("ab_test", {})
    ab_data["name"] = name
    ab_data["creator_id"] = message.from_user.id
    await state.update_data(ab_test=ab_data)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="2 варианта", callback_data="admin_abtests_variants:2"),
                InlineKeyboardButton(text="3 варианта", callback_data="admin_abtests_variants:3"),
            ],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_abtests_cancel")],
        ]
    )

    await state.set_state(AdminStates.waiting_for_ab_test_variant_count)
    await message.answer(
        "Выберите количество вариантов для теста.",
        reply_markup=keyboard,
    )


@router.callback_query(
    AdminStates.waiting_for_ab_test_variant_count,
    F.data.startswith("admin_abtests_variants:")
)
@broadcast_permission_required
async def admin_abtests_set_variant_count(callback: CallbackQuery, state: FSMContext):
    """Persist number of variants and request first variant content."""
    try:
        _, raw_count = callback.data.split(":")
        variant_count = int(raw_count)
    except (ValueError, AttributeError):
        await callback.answer("⚠️ Выберите количество вариантов кнопкой.", show_alert=True)
        return

    if variant_count not in (2, 3):
        await callback.answer("⚠️ Допустимо выбирать 2 или 3 варианта.", show_alert=True)
        return

    data = await state.get_data()
    ab_data = data.get("ab_test", {})
    ab_data["total_variants"] = variant_count
    ab_data["current_index"] = 0
    ab_data["variants"] = []
    ab_data.pop("pending_variant", None)
    await state.update_data(ab_test=ab_data)

    variant_label = _get_variant_code(0)
    await state.set_state(AdminStates.waiting_for_ab_test_variant_content)
    await callback.message.edit_text(
        f"Вариант {variant_label}.\n"
        "Отправьте <b>одним сообщением</b> текст и вложения для этого варианта.\n\n"
        "Поддерживаются текст, фото, видео, документы. После отправки вы настроите CTA-кнопки.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_abtests_cancel")]]
        ),
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_ab_test_variant_content)
@broadcast_permission_required
async def admin_abtests_collect_variant_content(message: Message, state: FSMContext):
    """Capture message content for current variant."""
    if _is_cancel_text(message.text or message.caption):
        await state.clear()
        await message.answer("❌ Создание теста отменено.")
        return

    items = _extract_broadcast_items(message)
    fallback_body = message.html_text or message.text or message.html_caption or message.caption or ""
    body = _extract_body_from_items(items, fallback_body)

    data = await state.get_data()
    ab_data = data.get("ab_test", {})
    current_index = int(ab_data.get("current_index", 0))
    variant_entry = {
        "code": _get_variant_code(current_index),
        "body": body,
        "content": items,
        "buttons": [],
    }
    ab_data["pending_variant"] = variant_entry
    await state.update_data(ab_test=ab_data)

    await state.set_state(AdminStates.waiting_for_ab_test_variant_buttons)
    await message.answer(
        "Добавьте CTA-кнопки для этого варианта.\n"
        "Формат: <code>Текст кнопки | действие</code>\n"
        "• Для ссылок можно указать полную ссылку или <code>Текст | url:https://...</code>\n"
        "• Для внутренних действий: <code>Текст | callback_data</code>\n\n"
        "Отправьте «нет», если кнопки не требуются.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_abtests_cancel")]]
        ),
    )


@router.message(AdminStates.waiting_for_ab_test_variant_buttons)
@broadcast_permission_required
async def admin_abtests_collect_variant_buttons(message: Message, state: FSMContext):
    """Handle CTA buttons definition for current variant."""
    if _is_cancel_text(message.text):
        await state.clear()
        await message.answer("❌ Создание теста отменено.")
        return

    data = await state.get_data()
    ab_data = data.get("ab_test", {})
    pending_variant = ab_data.get("pending_variant")
    if not pending_variant:
        await message.answer("⚠️ Не удалось найти вариант. Начните заново /cancel.")
        return

    raw_text = (message.text or "").strip()
    buttons: List[Dict[str, str]] = []
    if raw_text and raw_text.lower() not in {"нет", "no", "-"}:
        try:
            buttons = _parse_cta_buttons(raw_text)
        except ValueError as err:
            await message.answer(f"⚠️ {err}\nПопробуйте снова или отправьте «нет».")
            return

    pending_variant["buttons"] = buttons
    ab_data.setdefault("variants", []).append(pending_variant)
    ab_data["pending_variant"] = None
    ab_data["current_index"] = int(ab_data.get("current_index", 0)) + 1
    await state.update_data(ab_test=ab_data)

    if ab_data["current_index"] < ab_data.get("total_variants", 0):
        variant_label = _get_variant_code(ab_data["current_index"])
        await state.set_state(AdminStates.waiting_for_ab_test_variant_content)
        await message.answer(
            f"Вариант {variant_label}. Отправьте содержимое одним сообщением.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_abtests_cancel")]]
            ),
        )
        return

    preview_text = _build_ab_test_preview_text(ab_data.get("name", "Без названия"), ab_data["variants"])
    await state.update_data(ab_test=ab_data)
    await state.set_state(AdminStates.waiting_for_ab_test_confirmation)
    await message.answer(
        preview_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Запустить тест", callback_data="admin_abtests_confirm")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_abtests_cancel")],
            ]
        ),
    )


@router.callback_query(
    AdminStates.waiting_for_ab_test_confirmation,
    F.data == "admin_abtests_confirm",
)
@broadcast_permission_required
async def admin_abtests_confirm(callback: CallbackQuery, state: FSMContext):
    """Finalize A/B test creation and trigger delivery."""
    data = await state.get_data()
    ab_data = data.get("ab_test") or {}
    variants_data = ab_data.get("variants") or []
    name = ab_data.get("name")

    if not name or not variants_data:
        await state.clear()
        await callback.answer("⚠️ Данные теста не найдены, начните заново.", show_alert=True)
        return

    variant_defs = []
    for idx, variant in enumerate(variants_data):
        code = variant.get("code") or _get_variant_code(idx)
        title = f"{code}: {_summarize_text(variant.get('body') or '', 40)}"
        variant_defs.append(
            VariantDefinition(
                title=title,
                body=variant.get("body") or "",
                content=variant.get("content"),
                buttons=variant.get("buttons"),
                code=code,
            )
        )

    delivery_summary: Dict[str, Any] = {}
    analysis: Dict[str, Any] = {}
    job_id: Optional[str] = None

    try:
        async for session in get_db():
            seller_logger.info("ab_test.create.start", test_name=name, variants=len(variant_defs))
            logger.info(
                "ab_test.create.start",
                extra={"test_name": name, "variants": len(variant_defs)},
            )
            ab_service = ABTestingService(session)
            ab_test = await ab_service.create_test(
                name=name,
                creator_user_id=callback.from_user.id,
                variants=variant_defs,
                start_immediately=False,
            )

            delivery_summary = await ab_service.start_test(
                ab_test.id,
                bot=callback.bot,
                send_messages=True,
                throttle=0.1,
            )
            seller_logger.info("ab_test.start.result", test_id=ab_test.id, delivery=delivery_summary)
            logger.info(
                "ab_test.start.result",
                extra={"test_id": ab_test.id, "delivery": delivery_summary},
            )

            summary_time = datetime.now(timezone.utc) + timedelta(hours=24)
            job_id = await scheduler_service.schedule_ab_test_summary(ab_test.id, summary_time)
            if job_id:
                ab_test.notification_job_id = job_id

            analysis = await ab_service.analyze_test_results(ab_test.id)
            seller_logger.info(
                "ab_test.analysis.result",
                test_id=ab_test.id,
                analysis_error=analysis.get("error"),
                variants_count=len(analysis.get("variants", [])),
            )
            logger.info(
                "ab_test.analysis.result",
                extra={
                    "test_id": ab_test.id,
                    "analysis_error": analysis.get("error"),
                    "variants_count": len(analysis.get("variants", [])),
                },
            )

            await session.flush()
            await session.commit()
            break
    except Exception as exc:
        seller_logger.error("ab_test.error", error=str(exc), exc_info=True)
        async for session in get_db():
            await session.rollback()
            break
        await callback.answer("❌ Не удалось запустить тест", show_alert=True)
        return

    await state.clear()
    delivery = delivery_summary.get("delivery", {})
    assignments = delivery_summary.get("assignments", 0)
    sent = delivery.get("sent", 0)
    failed = delivery.get("failed", 0)

    lines = [
        "✅ <b>A/B тест запущен!</b>",
        f"Название: {escape(name)}",
        f"Охват (30%): {assignments}",
        f"Успешно отправлено: {sent} | Ошибок: {failed}",
    ]

    if job_id is None:
        lines.append("⚠️ Не удалось запланировать автоматическое уведомление — проверьте планировщик.")

    if analysis.get("variants"):
        lines.append("")
        lines.append("Текущие показатели:")
        for variant in analysis["variants"]:
            lines.append(
                f"• {variant.get('variant')}: доставлено {variant.get('delivered', 0)}, "
                f"CTR {format_percent(variant.get('ctr'))}, "
                f"CR {format_percent(variant.get('cr'))}"
            )

    lines.append("")
    lines.append("📬 Итоговый отчёт придёт автоматически через 24 часа.")

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Перейти к результатам", callback_data="admin_abtests_results")],
            [InlineKeyboardButton(text="⬅️ В меню A/B тестов", callback_data="admin_abtests")],
        ]
    )

    await callback.message.edit_text("\n".join(lines), parse_mode="HTML", reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "admin_abtests_cancel")
async def admin_abtests_cancel(callback: CallbackQuery, state: FSMContext):
    """Abort A/B test creation wizard."""
    await state.clear()
    await callback.message.edit_text(
        "❌ Создание A/B теста отменено.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ В меню A/B тестов", callback_data="admin_abtests")]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_abtests_results")
@admin_required
async def admin_abtests_results(callback: CallbackQuery):
    """Show list of available A/B tests."""
    try:
        async for session in get_db():
            admin_repo = AdminRepository(session)
            admin_record = await admin_repo.get_by_telegram_id(callback.from_user.id)
            role_value = getattr(admin_record, "role", None)
            can_view_all = False
            if role_value in {AdminRole.ADMIN, AdminRole.OWNER}:
                can_view_all = True
            else:
                can_view_all = await admin_repo.can_manage_broadcasts(callback.from_user.id)

            stmt = select(ABTest).order_by(ABTest.created_at.desc()).limit(12)
            if not can_view_all:
                stmt = stmt.where(ABTest.creator_user_id == callback.from_user.id)

            tests = list((await session.execute(stmt)).scalars().all())
            break
    except SQLAlchemyError as exc:
        logger.warning("ab_tests.list_failed", error=str(exc))
        await callback.message.edit_text(
            "🧪 Список A/B тестов недоступен. Выполните миграции базы данных и попробуйте снова.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]]
            ),
        )
        await callback.answer()
        return

        if not tests:
            await callback.message.edit_text(
                "🧪 <b>A/B тесты</b>\n\nПока нет тестов, доступных для просмотра.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_abtests")]]
                ),
            )
            await callback.answer()
            return

        lines = ["🧪 <b>Список A/B тестов</b>", ""]
        builder = InlineKeyboardBuilder()

        for test in tests:
            status_value = test.status if isinstance(test.status, str) else getattr(test.status, "value", str(test.status))
            status_label = AB_STATUS_LABELS.get(clean_enum_value(status_value), status_value)
            created = _format_datetime(test.created_at)
            creator_hint = f"(initiator: {test.creator_user_id})" if can_view_all else ""
            lines.append(f"• #{test.id} {escape(test.name)} — {status_label} {creator_hint}")
            lines.append(f"  Запущен: {created}")
            lines.append("")

            builder.row(
                InlineKeyboardButton(
                    text=f"#{test.id} {test.name[:20]}",
                    callback_data=f"admin_abtests_result:{test.id}",
                )
            )

        builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_abtests"))

        await callback.message.edit_text(
            "\n".join(lines).strip(),
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        await callback.answer()

    except Exception:
        logger.exception("Error showing A/B test list")
        await callback.answer("❌ Не удалось получить список тестов", show_alert=True)


@router.callback_query(F.data.startswith("admin_abtests_result:"))
@admin_required
async def admin_abtests_result_detail(callback: CallbackQuery):
    """Show detailed metrics for specific A/B test."""
    try:
        _, raw_test_id = callback.data.split(":")
        test_id = int(raw_test_id)
    except (ValueError, AttributeError):
        await callback.answer("⚠️ Некорректный идентификатор теста.", show_alert=True)
        return

    try:
        async for session in get_db():
            admin_repo = AdminRepository(session)
            admin_record = await admin_repo.get_by_telegram_id(callback.from_user.id)
            role_value = getattr(admin_record, "role", None)
            can_view_all = role_value in {AdminRole.ADMIN, AdminRole.OWNER}

            ab_service = ABTestingService(session)
            analysis = await ab_service.analyze_test_results(test_id)

            if analysis.get("error") == "ab_tables_missing":
                await callback.message.edit_text(
                    "🧪 Детализация теста недоступна. Требуется выполнить миграции БД.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_abtests_results")]]
                    ),
                )
                await callback.answer()
                return

            if analysis.get("error"):
                await callback.message.edit_text(
                    "⚠️ Не удалось загрузить статистику A/B теста.",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_abtests_results")]]
                    ),
                )
                await callback.answer()
                return

            creator_id = analysis.get("creator_user_id")
            if not can_view_all and creator_id and creator_id != callback.from_user.id:
                await callback.answer("❌ У вас нет доступа к этому тесту.", show_alert=True)
                return

            detail_text = _build_ab_test_result_text(analysis)
            break

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"admin_abtests_result:{test_id}")],
                [InlineKeyboardButton(text="⬅️ К списку тестов", callback_data="admin_abtests_results")],
            ]
        )

        await callback.message.edit_text(
            detail_text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        await callback.answer()

    except Exception:
        logger.exception("Error showing A/B test detail")
        await callback.answer("❌ Не удалось загрузить данные теста", show_alert=True)


# Materials Management
@router.callback_query(F.data == "admin_materials")
@role_required(AdminRole.EDITOR)
async def admin_materials(callback: CallbackQuery):
    """Show materials management menu."""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📚 Последние материалы", callback_data="material_list")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="material_stats")],
            [InlineKeyboardButton(text="🏷️ Популярные теги", callback_data="material_tags")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")],
        ]
    )

    await callback.message.edit_text(
        "📚 <b>Управление материалами</b>\n\n"
        "Доступные действия:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "material_list")
@role_required(AdminRole.EDITOR)
async def material_list(callback: CallbackQuery):
    """Show latest materials for admins."""
    try:
        async for session in get_db():
            materials = await _get_materials_for_admin(session, limit=10)
            break

        if not materials:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_materials")]]
            )
            await callback.message.edit_text(
                "📚 <b>Материалы</b>\n\n"
                "Пока нет опубликованных материалов.",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            await callback.answer()
            return

        lines: List[str] = ["📚 <b>Последние материалы</b>", ""]
        builder = InlineKeyboardBuilder()

        for material in materials:
            status_label = _material_badge(material)
            updated = _format_datetime(material.updated_at)
            segments = _material_segments(material)
            lines.append(f"<b>{escape(material.title)}</b> — {status_label}")
            lines.append(f"ID: <code>{material.id}</code>")
            lines.append(f"Сегменты: {segments}")
            lines.append(f"Обновлён: {updated}")
            lines.append("")

            button_text = f"#{material.id[:4]} {material.title[:20]}"
            builder.row(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"material_detail:{material.id}",
                )
            )

        builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_materials"))

        await callback.message.edit_text(
            "\n".join(lines).strip(),
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
            disable_web_page_preview=True,
        )
        await callback.answer()

    except Exception as exc:  # pragma: no cover - logging
        logger.exception("Error loading material list", exc_info=exc)
        await callback.answer("❌ Ошибка при загрузке материалов", show_alert=True)


@router.callback_query(F.data.startswith("material_detail:"))
@role_required(AdminRole.EDITOR)
async def material_detail(callback: CallbackQuery):
    """Show material details."""
    material_id = callback.data.split(":", 1)[1]
    try:
        async for session in get_db():
            material = await _get_material_by_id(session, material_id)
            break

        if not material:
            await callback.answer("Материал не найден", show_alert=True)
            return

        text, markup = _build_material_detail(material)
        await callback.message.edit_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        await callback.answer()

    except Exception as exc:  # pragma: no cover
        logger.exception("Error showing material detail", material_id=material_id, exc_info=exc)
        await callback.answer("❌ Ошибка при загрузке материала", show_alert=True)


@router.callback_query(F.data.startswith("material_toggle:"))
@role_required(AdminRole.EDITOR)
async def material_toggle(callback: CallbackQuery):
    """Toggle material publication status."""
    try:
        _, material_id, target_status = callback.data.split(":", 2)
        if target_status not in {status.value for status in MaterialStatus}:
            await callback.answer("❌ Некорректный статус", show_alert=True)
            return

        async for session in get_db():
            material = await _get_material_by_id(session, material_id)

            if not material:
                await callback.answer("Материал не найден", show_alert=True)
                return

            material.status = target_status
            material.updated_at = datetime.now(timezone.utc)
            await session.flush()
            await session.refresh(material)
            await session.commit()

            text, markup = _build_material_detail(material)
            await callback.message.edit_text(
                text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
            await callback.answer("Статус обновлён")
            break

    except Exception as exc:  # pragma: no cover
        logger.exception("Error toggling material", exc_info=exc)
        await callback.answer("❌ Ошибка при обновлении статуса", show_alert=True)


@router.callback_query(F.data == "material_stats")
@role_required(AdminRole.EDITOR)
async def material_stats(callback: CallbackQuery):
    """Show material statistics."""
    try:
        async for session in get_db():
            repo = MaterialRepository(session)
            stats = await repo.get_material_stats()
            break

        total = stats.get("total", 0)
        active = stats.get("active", 0)
        inactive = stats.get("inactive", total - active)
        by_type = stats.get("by_type", {})

        lines = [
            "📊 <b>Статистика материалов</b>",
            "",
            f"Всего: {total}",
            f"Активных: {active}",
            f"Архив/черновики: {inactive}",
        ]

        if by_type:
            lines.append("\n<b>По категориям</b>")
            for material_type, count in by_type.items():
                lines.append(f"• {material_type or 'не указано'} — {count}")

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_materials")]]
        )

        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        await callback.answer()

    except Exception as exc:
        logger.exception("Error showing material stats", exc_info=exc)
        await callback.answer("❌ Ошибка при загрузке статистики", show_alert=True)


@router.callback_query(F.data == "material_tags")
@role_required(AdminRole.EDITOR)
async def material_tags(callback: CallbackQuery):
    """Show popular material tags."""
    try:
        async for session in get_db():
            repo = MaterialRepository(session)
            tags = await repo.get_popular_tags(limit=10)
            break

        if not tags:
            text = "🏷️ <b>Популярные теги</b>\n\nНет данных о тегах."
        else:
            lines = ["🏷️ <b>Популярные теги</b>", ""]
            for tag, count in tags:
                lines.append(f"• <code>{escape(tag)}</code> — {count}")
            text = "\n".join(lines)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_materials")]]
        )

        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        await callback.answer()

    except Exception as exc:
        logger.exception("Error loading material tags", exc_info=exc)
        await callback.answer("❌ Ошибка при загрузке тегов", show_alert=True)


# Product Management
@router.callback_query(F.data == "admin_products")
@role_required(AdminRole.ADMIN)
async def admin_products(callback: CallbackQuery):
    """Show product management menu."""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🆕 Новый продукт", callback_data="product_create")],
            [InlineKeyboardButton(text="💰 Все продукты", callback_data="product_list")],
            [InlineKeyboardButton(text="📊 Статистика", callback_data="product_stats")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")],
        ]
    )

    await callback.message.edit_text(
        "💰 <b>Управление продуктами</b>\n\nВыберите действие:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


@router.callback_query(F.data == "product_list")
@role_required(AdminRole.ADMIN)
async def product_list(callback: CallbackQuery):
    """Show product list."""
    try:
        async for session in get_db():
            result = await session.execute(
                select(Product).order_by(Product.is_active.desc(), Product.price)
            )
            products = result.scalars().all()
            break

        if not products:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_products")]]
            )
            await callback.message.edit_text(
                "💰 <b>Продукты</b>\n\nПока нет добавленных продуктов.",
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            await callback.answer()
            return

        lines = ["💰 <b>Список продуктов</b>", ""]
        builder = InlineKeyboardBuilder()
        for product in products:
            status_label = PRODUCT_STATUS_LABELS.get(product.is_active, "—")
            price = _format_currency(product.price)
            lines.append(f"<b>{escape(product.name)}</b> — {price} ({status_label})")
            lines.append(f"Код: <code>{escape(product.code)}</code>")
            lines.append("")
            button_text = f"#{product.id} {product.name[:18]}"
            builder.row(
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"product_detail:{product.id}",
                )
            )

        builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_products"))

        await callback.message.edit_text(
            "\n".join(lines).strip(),
            parse_mode="HTML",
            reply_markup=builder.as_markup(),
        )
        await callback.answer()

    except Exception as exc:
        logger.exception("Error listing products", exc_info=exc)
        await callback.answer("❌ Ошибка при загрузке продуктов", show_alert=True)


@router.callback_query(F.data.startswith("product_detail:"))
@role_required(AdminRole.ADMIN)
async def product_detail(callback: CallbackQuery, state: FSMContext):
    """Show product details."""
    product_id = int(callback.data.split(":", 1)[1])
    try:
        async for session in get_db():
            product = await _get_product_by_id(session, product_id)
            break

        if not product:
            await callback.answer("Продукт не найден", show_alert=True)
            return

        text, markup = _build_product_detail(product)
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await state.update_data(product_detail_message_id=callback.message.message_id, product_detail_chat_id=callback.message.chat.id)
        await callback.answer()

    except Exception as exc:
        logger.exception("Error showing product detail", exc_info=exc)
        await callback.answer("❌ Ошибка при загрузке продукта", show_alert=True)


@router.callback_query(F.data == "product_create")
@role_required(AdminRole.ADMIN)
async def product_create(callback: CallbackQuery, state: FSMContext):
    """Start product creation flow."""
    await state.clear()
    await state.set_state(AdminStates.waiting_for_product_code)
    await callback.message.edit_text(
        "🆕 <b>Новый продукт</b>\n\nВведите уникальный код (латиница, цифры, -/_):",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_product_code)
@role_required(AdminRole.ADMIN)
async def product_create_code(message: Message, state: FSMContext):
    code = message.text.strip()
    normalized = code.lower().replace(" ", "_")
    if not normalized or any(ch for ch in normalized if ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_"):
        await message.answer("❌ Код может содержать только латиницу, цифры, '-', '_'. Введите код ещё раз:")
        return

    async for session in get_db():
        repo = ProductRepository(session)
        existing = await repo.get_by_code(normalized)
        break

    if existing:
        await message.answer("❌ Такой код уже используется. Введите другой код:")
        return

    await state.update_data(product_code=normalized)
    await state.set_state(AdminStates.waiting_for_product_name)
    await message.answer("Введите название продукта:")


@router.message(AdminStates.waiting_for_product_name)
@role_required(AdminRole.ADMIN)
async def product_create_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("❌ Название не может быть пустым. Попробуйте снова:")
        return
    await state.update_data(product_name=name)
    await state.set_state(AdminStates.waiting_for_product_price)
    await message.answer("Введите цену в рублях (например, 49900):")


@router.message(AdminStates.waiting_for_product_price)
@role_required(AdminRole.ADMIN)
async def product_create_price(message: Message, state: FSMContext):
    try:
        normalized = message.text.replace(" ", "").replace(",", ".")
        price = Decimal(normalized)
        if price <= 0:
            raise InvalidOperation
    except Exception:
        await message.answer("❌ Введите корректную цену (число больше 0):")
        return

    await state.update_data(product_price=str(price))
    await state.set_state(AdminStates.waiting_for_product_currency)
    await message.answer(
        "Введите валюту цены (например, RUB, USD). Оставьте пустым для RUB:",
    )


@router.message(AdminStates.waiting_for_product_currency)
@role_required(AdminRole.ADMIN)
async def product_create_currency(message: Message, state: FSMContext):
    currency = (message.text or "").strip().upper() or "RUB"
    if not re.fullmatch(r"[A-Z]{3,5}", currency):
        await message.answer("❌ Валюта должна быть указана в формате ISO, например RUB или USD.")
        return

    await state.update_data(product_currency=currency)
    await state.set_state(AdminStates.waiting_for_product_short_desc)
    await message.answer(
        "Напишите короткое описание (1–2 предложения) как увидит пользователь.\n"
        "Если хотите пропустить, отправьте '-'.",
    )


@router.message(AdminStates.waiting_for_product_short_desc)
@role_required(AdminRole.ADMIN)
async def product_create_short_desc(message: Message, state: FSMContext):
    short_desc_raw = (message.text or "").strip()
    short_desc = None if short_desc_raw in {"", "-"} else short_desc_raw
    await state.update_data(product_short_desc=short_desc)
    await state.set_state(AdminStates.waiting_for_product_value_props)
    await message.answer(
        "Перечислите 2–4 ключевых выгоды через запятую или каждую с новой строки.\n"
        "Можно отправить JSON-массив. Чтобы пропустить, отправьте '-'.",
    )


def _parse_value_props_payload(raw: str) -> List[str]:
    """Parse admin input into list of value props."""
    candidate = raw.strip()
    if not candidate or candidate == "-":
        return []
    if candidate.startswith("["):
        try:
            data = json.loads(candidate)
            if isinstance(data, list):
                return [str(item).strip() for item in data if str(item).strip()]
        except json.JSONDecodeError:
            pass
    separators = "\n;,|"
    for sep in separators:
        if sep in candidate:
            parts = [part.strip() for part in candidate.split(sep) if part.strip()]
            if parts:
                return parts
    return [candidate]


@router.message(AdminStates.waiting_for_product_value_props)
@role_required(AdminRole.ADMIN)
async def product_create_value_props(message: Message, state: FSMContext):
    value_props = _parse_value_props_payload(message.text or "")
    await state.update_data(product_value_props=value_props)
    await state.set_state(AdminStates.waiting_for_product_description)
    await message.answer("Введите подробное описание продукта (или '-' чтобы пропустить):")


@router.message(AdminStates.waiting_for_product_description)
@role_required(AdminRole.ADMIN)
async def product_create_description(message: Message, state: FSMContext):
    description = message.text.strip()
    if description == "-":
        description = ""
    await state.update_data(product_description=description)
    await state.set_state(AdminStates.waiting_for_product_landing_url)
    await message.answer("Если у продукта есть лендинг, отправьте ссылку. Иначе отправьте '-' или оставьте поле пустым:")


@router.message(AdminStates.waiting_for_product_landing_url)
@role_required(AdminRole.ADMIN)
async def product_create_finalize(message: Message, state: FSMContext):
    landing_url = message.text.strip()
    if landing_url in {"-", "", "нет", "Нет"}:
        landing_url = None

    data = await state.get_data()
    code = data.get("product_code")
    name = data.get("product_name")
    price = Decimal(data.get("product_price", "0"))
    description = data.get("product_description") or None
    currency = data.get("product_currency") or "RUB"
    short_desc = data.get("product_short_desc")
    value_props = data.get("product_value_props") or []

    try:
        async for session in get_db():
            repo = ProductRepository(session)
            existing = await repo.get_by_code(code)
            if existing:
                await message.answer("❌ Код уже используется другим продуктом. Запустите создание заново и введите другой код.")
                await state.clear()
                return

            product = await repo.create_product(
                code=code,
                name=name,
                price=price,
                description=description,
                currency=currency,
                short_desc=short_desc,
                value_props=value_props,
                landing_url=landing_url,
                payment_landing_url=landing_url,
                slug=code,
            )
            await session.flush()
            await session.refresh(product)
            await session.commit()

            text, markup = _build_product_detail(product)
            await message.answer("✅ Продукт создан!", parse_mode="HTML")
            await message.answer(text, reply_markup=markup, parse_mode="HTML")
            break

    except Exception as exc:
        logger.exception("Error creating product", exc_info=exc)
        await message.answer("❌ Ошибка при создании продукта. Попробуйте позже.")

    await state.clear()


@router.callback_query(F.data.startswith("product_toggle:"))
@role_required(AdminRole.ADMIN)
async def product_toggle(callback: CallbackQuery):
    """Toggle product active flag."""
    product_id = int(callback.data.split(":", 1)[1])
    try:
        async for session in get_db():
            product = await _get_product_by_id(session, product_id)
            if not product:
                await callback.answer("Продукт не найден", show_alert=True)
                return
            product.is_active = not product.is_active
            await session.flush()
            await session.refresh(product)
            await session.commit()
            text, markup = _build_product_detail(product)
            await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
            await callback.answer("Статус обновлён")
            break
    except Exception as exc:
        logger.exception("Error toggling product", exc_info=exc)
        await callback.answer("❌ Ошибка при обновлении продукта", show_alert=True)


@router.callback_query(F.data.startswith("product_edit_currency:"))
@role_required(AdminRole.ADMIN)
async def product_edit_currency(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":", 1)[1])
    await state.update_data(
        product_edit_id=product_id,
        product_detail_message_id=callback.message.message_id,
        product_detail_chat_id=callback.message.chat.id,
    )
    await state.set_state(AdminStates.waiting_for_product_edit_currency)
    await callback.message.answer("Введите валюту (например, RUB, USD):")
    await callback.answer()


@router.message(AdminStates.waiting_for_product_edit_currency)
@role_required(AdminRole.ADMIN)
async def product_edit_currency_commit(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get("product_edit_id")
    currency = (message.text or "").strip().upper() or "RUB"
    if not re.fullmatch(r"[A-Z]{3,5}", currency):
        await message.answer("❌ Некорректная валюта. Используйте формат например RUB или USD.")
        return

    try:
        async for session in get_db():
            product = await _get_product_by_id(session, product_id)
            if not product:
                await message.answer("❌ Продукт не найден")
                await state.clear()
                return
            product.currency = currency
            await session.flush()
            await session.refresh(product)
            await session.commit()
            text, markup = _build_product_detail(product)
            await message.bot.edit_message_text(
                text,
                chat_id=data.get("product_detail_chat_id"),
                message_id=data.get("product_detail_message_id"),
                reply_markup=markup,
                parse_mode="HTML",
            )
            await message.answer("✅ Валюта обновлена")
            break
    except Exception as exc:
        logger.exception("Error updating product currency", exc_info=exc)
        await message.answer("❌ Ошибка при обновлении валюты")

    await state.clear()


@router.callback_query(F.data.startswith("product_edit_short:"))
@role_required(AdminRole.ADMIN)
async def product_edit_short(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":", 1)[1])
    await state.update_data(
        product_edit_id=product_id,
        product_detail_message_id=callback.message.message_id,
        product_detail_chat_id=callback.message.chat.id,
    )
    await state.set_state(AdminStates.waiting_for_product_edit_short_desc)
    await callback.message.answer("Введите новое короткое описание (или '-' для очистки):")
    await callback.answer()


@router.message(AdminStates.waiting_for_product_edit_short_desc)
@role_required(AdminRole.ADMIN)
async def product_edit_short_commit(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get("product_edit_id")
    short_desc = (message.text or "").strip()
    if short_desc in {"", "-"}:
        short_desc = None

    try:
        async for session in get_db():
            product = await _get_product_by_id(session, product_id)
            if not product:
                await message.answer("❌ Продукт не найден")
                await state.clear()
                return
            product.short_desc = short_desc
            await session.flush()
            await session.refresh(product)
            await session.commit()
            text, markup = _build_product_detail(product)
            await message.bot.edit_message_text(
                text,
                chat_id=data.get("product_detail_chat_id"),
                message_id=data.get("product_detail_message_id"),
                reply_markup=markup,
                parse_mode="HTML",
            )
            await message.answer("✅ Короткое описание обновлено")
            break
    except Exception as exc:
        logger.exception("Error updating short description", exc_info=exc)
        await message.answer("❌ Ошибка при обновлении описания")

    await state.clear()


@router.callback_query(F.data.startswith("product_edit_value:"))
@role_required(AdminRole.ADMIN)
async def product_edit_value(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":", 1)[1])
    await state.update_data(
        product_edit_id=product_id,
        product_detail_message_id=callback.message.message_id,
        product_detail_chat_id=callback.message.chat.id,
    )
    await state.set_state(AdminStates.waiting_for_product_edit_value_props)
    await callback.message.answer(
        "Перечислите выгоды через запятую/строки или отправьте JSON-массив. '-' очистит список.",
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_product_edit_value_props)
@role_required(AdminRole.ADMIN)
async def product_edit_value_commit(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get("product_edit_id")
    value_props = _parse_value_props_payload(message.text or "")

    try:
        async for session in get_db():
            product = await _get_product_by_id(session, product_id)
            if not product:
                await message.answer("❌ Продукт не найден")
                await state.clear()
                return
            product.value_props = value_props
            await session.flush()
            await session.refresh(product)
            await session.commit()
            text, markup = _build_product_detail(product)
            await message.bot.edit_message_text(
                text,
                chat_id=data.get("product_detail_chat_id"),
                message_id=data.get("product_detail_message_id"),
                reply_markup=markup,
                parse_mode="HTML",
            )
            await message.answer("✅ Ключевые выгоды обновлены")
            break
    except Exception as exc:
        logger.exception("Error updating value props", exc_info=exc)
        await message.answer("❌ Ошибка при обновлении списка выгод")

    await state.clear()


@router.callback_query(F.data.startswith("product_edit_landing:"))
@role_required(AdminRole.ADMIN)
async def product_edit_landing(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":", 1)[1])
    await state.update_data(
        product_edit_id=product_id,
        product_detail_message_id=callback.message.message_id,
        product_detail_chat_id=callback.message.chat.id,
    )
    await state.set_state(AdminStates.waiting_for_product_edit_landing)
    await callback.message.answer("Введите ссылку на лендинг (или '-' для очистки):")
    await callback.answer()


@router.message(AdminStates.waiting_for_product_edit_landing)
@role_required(AdminRole.ADMIN)
async def product_edit_landing_commit(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get("product_edit_id")
    landing = (message.text or "").strip()
    if landing in {"", "-"}:
        landing = None

    try:
        async for session in get_db():
            product = await _get_product_by_id(session, product_id)
            if not product:
                await message.answer("❌ Продукт не найден")
                await state.clear()
                return
            product.landing_url = landing
            if not product.payment_landing_url:
                product.payment_landing_url = landing
            await session.flush()
            await session.refresh(product)
            await session.commit()
            text, markup = _build_product_detail(product)
            await message.bot.edit_message_text(
                text,
                chat_id=data.get("product_detail_chat_id"),
                message_id=data.get("product_detail_message_id"),
                reply_markup=markup,
                parse_mode="HTML",
            )
            await message.answer("✅ Лендинг обновлён")
            break
    except Exception as exc:
        logger.exception("Error updating landing", exc_info=exc)
        await message.answer("❌ Ошибка при обновлении ссылки")

    await state.clear()


@router.callback_query(F.data.startswith("product_criteria:"))
@role_required(AdminRole.ADMIN)
async def product_criteria_menu(callback: CallbackQuery, state: FSMContext):
    """Show current product criteria and instructions."""
    product_id = int(callback.data.split(":", 1)[1])
    try:
        async for session in get_db():
            product = await _get_product_by_id(session, product_id)
            if not product:
                await callback.answer("Продукт не найден", show_alert=True)
                return

            survey_service = SurveyService(session)
            catalog = _build_survey_catalog(survey_service)
            reference_text = _format_survey_reference(catalog)
            current_rules = _format_criteria_table(product.criteria or [])

            keyboard = InlineKeyboardBuilder()
            keyboard.row(
                InlineKeyboardButton(
                    text="✏️ Редактировать",
                    callback_data=f"product_criteria_edit:{product.id}",
                )
            )
            keyboard.row(
                InlineKeyboardButton(
                    text="⬅️ К продукту",
                    callback_data=f"product_detail:{product.id}",
                )
            )

            message_text = (
                f"🧠 <b>{escape(product.name)}</b> — критерии анкеты\n\n"
                f"<b>Текущие правила:</b>\n<pre>{escape(current_rules)}</pre>\n"
                "<b>Формат:</b>\n"
                "Q1: 2,4\n"
                "Q3: 3(-1) // отрицательный ответ\n\n"
                "<b>Расшифровка вопросов:</b>\n"
                f"<pre>{escape(reference_text)}</pre>"
            )

            await callback.message.answer(message_text, parse_mode="HTML", reply_markup=keyboard.as_markup())
            break
    except Exception as exc:
        logger.exception("Error viewing product criteria", exc_info=exc)
        await callback.answer("❌ Ошибка загрузки критериев", show_alert=True)
        return

    await callback.answer()


@router.callback_query(F.data.startswith("product_criteria_edit:"))
@role_required(AdminRole.ADMIN)
async def product_criteria_edit(callback: CallbackQuery, state: FSMContext):
    """Prompt admin to send new criteria definition."""
    product_id = int(callback.data.split(":", 1)[1])
    await state.update_data(
        product_edit_id=product_id,
        product_detail_message_id=callback.message.message_id,
        product_detail_chat_id=callback.message.chat.id,
    )
    await state.set_state(AdminStates.waiting_for_product_criteria)
    await callback.message.answer(
        "Отправьте критерии в формате:\n"
        "Q1: 2,4\n"
        "Q3: 3(-1)\n\n"
        "Используйте запятую для нескольких ответов, (-1) для отрицательного веса.\n"
        "Можно добавить комментарий: Q2: 1(-1|note=слишком мало)\n\n"
        "Чтобы очистить все правила, отправьте '-'.",
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_product_criteria)
@role_required(AdminRole.ADMIN)
async def product_criteria_commit(message: Message, state: FSMContext):
    """Persist new criteria set."""
    data = await state.get_data()
    product_id = data.get("product_edit_id")
    payload = (message.text or "").strip()

    try:
        async for session in get_db():
            product = await _get_product_by_id(session, product_id)
            if not product:
                await message.answer("❌ Продукт не найден")
                await state.clear()
                return

            survey_service = SurveyService(session)
            catalog = _build_survey_catalog(survey_service)

            if payload in {"", "-"}:
                repo = ProductCriteriaRepository(session)
                await repo.delete_for_product(product.id)
                await session.commit()
                updated = await _get_product_by_id(session, product.id)
                text, markup = _build_product_detail(updated)
                await message.bot.edit_message_text(
                    text,
                    chat_id=data.get("product_detail_chat_id"),
                    message_id=data.get("product_detail_message_id"),
                    reply_markup=markup,
                    parse_mode="HTML",
                )
                await message.answer("✅ Критерии очищены")
                break

            try:
                parsed_entries = _parse_criteria_input(payload, catalog)
            except ValueError as parse_error:
                await message.answer(f"❌ Ошибка разбора:\n{parse_error}")
                return

            repo = ProductCriteriaRepository(session)
            await repo.replace_for_product(product.id, parsed_entries)
            await session.commit()

            updated = await _get_product_by_id(session, product.id)
            text, markup = _build_product_detail(updated)
            await message.bot.edit_message_text(
                text,
                chat_id=data.get("product_detail_chat_id"),
                message_id=data.get("product_detail_message_id"),
                reply_markup=markup,
                parse_mode="HTML",
            )
            await message.answer(
                "✅ Критерии сохранены\n\n"
                f"<pre>{escape(_format_criteria_table(updated.criteria))}</pre>",
                parse_mode="HTML",
            )
            break
    except Exception as exc:
        logger.exception("Error updating product criteria", exc_info=exc)
        await message.answer("❌ Не удалось сохранить критерии.")

    await state.clear()


@router.callback_query(F.data.startswith("product_match_check:"))
@role_required(AdminRole.ADMIN)
async def product_match_check(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":", 1)[1])
    await state.update_data(
        product_check_id=product_id,
        product_detail_message_id=callback.message.message_id,
        product_detail_chat_id=callback.message.chat.id,
    )
    await state.set_state(AdminStates.waiting_for_product_criteria_check_user)
    await callback.message.answer(
        "Введите ID пользователя (цифрами) или @username для проверки рекомендаций:",
    )
    await callback.answer()


@router.message(AdminStates.waiting_for_product_criteria_check_user)
@role_required(AdminRole.ADMIN)
async def product_match_check_commit(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get("product_check_id")
    query = (message.text or "").strip()
    if not query:
        await message.answer("❌ Введите корректный ID пользователя")
        return

    try:
        async for session in get_db():
            user: Optional[User] = None
            if query.lstrip("-").isdigit():
                user = await session.get(User, int(query))
            elif query.startswith("@"):
                user_repo = UserRepository(session)
                user = await user_repo.get_by_username(query[1:])
            else:
                await message.answer("❌ Введите числовой ID или @username")
                await state.clear()
                return

            if not user:
                await message.answer("❌ Пользователь не найден")
                await state.clear()
                return

            matching_service = ProductMatchingService(session)
            _, match_result = await matching_service.evaluate_for_user_id(
                user.id,
                trigger="admin_probe",
                limit=10,
                log_result=False,
            )

            candidate_lines = []
            for index, candidate in enumerate(match_result.candidates, start=1):
                highlight = " ✅" if candidate.product.id == product_id else ""
                candidate_lines.append(
                    f"{index}. {candidate.product.name} — {candidate.score:.2f}{highlight}"
                )
            if not candidate_lines:
                candidate_lines.append("Нет активных продуктов для рекомендаций")

            best_line = "Лучший продукт не найден"
            if match_result.best_product:
                best_line = (
                    f"Top-1: {match_result.best_product.name}"
                    f" (score {match_result.score:.2f})"
                )

            explanation = (match_result.explanation or "").replace("\n", " ").strip()

            lines = [
                "🧠 <b>Проверка рекомендаций</b>",
                f"Пользователь: <code>{user.id}</code> ({escape(user.username) if user.username else '—'})",
                f"Сегмент: {user.segment or '—'}",
                best_line,
                f"Причина: {escape(explanation) if explanation else '—'}",
                "",
                "Top кандидаты:",
            ]
            lines.extend(candidate_lines)

            await message.answer("\n".join(lines), parse_mode="HTML")
            break
    except Exception as exc:
        logger.exception("Error checking product match", exc_info=exc)
        await message.answer("❌ Не удалось выполнить проверку")

    await state.clear()


@router.callback_query(F.data.startswith("product_edit_price:"))
@role_required(AdminRole.ADMIN)
async def product_edit_price(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":", 1)[1])
    await state.update_data(product_edit_id=product_id, product_detail_message_id=callback.message.message_id, product_detail_chat_id=callback.message.chat.id)
    await state.set_state(AdminStates.waiting_for_product_edit_price)
    await callback.message.answer("Введите новую цену в рублях:")
    await callback.answer()


@router.message(AdminStates.waiting_for_product_edit_price)
@role_required(AdminRole.ADMIN)
async def product_edit_price_commit(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get("product_edit_id")
    try:
        new_price = Decimal(message.text.replace(" ", "").replace(",", "."))
        if new_price <= 0:
            raise InvalidOperation
    except Exception:
        await message.answer("❌ Некорректная цена. Введите число больше 0:")
        return

    try:
        async for session in get_db():
            product = await _get_product_by_id(session, product_id)
            if not product:
                await message.answer("❌ Продукт не найден")
                await state.clear()
                return
            product.price = new_price
            await session.flush()
            await session.refresh(product)
            await session.commit()
            text, markup = _build_product_detail(product)
            await message.bot.edit_message_text(
                text,
                chat_id=data.get("product_detail_chat_id"),
                message_id=data.get("product_detail_message_id"),
                reply_markup=markup,
                parse_mode="HTML",
            )
            await message.answer("✅ Цена обновлена")
            break
    except Exception as exc:
        logger.exception("Error updating product price", exc_info=exc)
        await message.answer("❌ Ошибка при обновлении цены")

    await state.clear()


@router.callback_query(F.data.startswith("product_edit_description:"))
@role_required(AdminRole.ADMIN)
async def product_edit_description(callback: CallbackQuery, state: FSMContext):
    product_id = int(callback.data.split(":", 1)[1])
    await state.update_data(product_edit_id=product_id, product_detail_message_id=callback.message.message_id, product_detail_chat_id=callback.message.chat.id)
    await state.set_state(AdminStates.waiting_for_product_edit_description)
    await callback.message.answer("Введите новое описание (или '-' для очистки):")
    await callback.answer()


@router.message(AdminStates.waiting_for_product_edit_description)
@role_required(AdminRole.ADMIN)
async def product_edit_description_commit(message: Message, state: FSMContext):
    data = await state.get_data()
    product_id = data.get("product_edit_id")
    description = message.text.strip()
    if description in {"-", ""}:
        description = None

    try:
        async for session in get_db():
            product = await _get_product_by_id(session, product_id)
            if not product:
                await message.answer("❌ Продукт не найден")
                await state.clear()
                return
            product.description = description
            await session.flush()
            await session.refresh(product)
            await session.commit()
            text, markup = _build_product_detail(product)
            await message.bot.edit_message_text(
                text,
                chat_id=data.get("product_detail_chat_id"),
                message_id=data.get("product_detail_message_id"),
                reply_markup=markup,
                parse_mode="HTML",
            )
            await message.answer("✅ Описание обновлено")
            break
    except Exception as exc:
        logger.exception("Error updating product description", exc_info=exc)
        await message.answer("❌ Ошибка при обновлении описания")

    await state.clear()


@router.callback_query(F.data == "product_stats")
@role_required(AdminRole.ADMIN)
async def product_stats(callback: CallbackQuery):
    """Show product statistics."""
    try:
        async for session in get_db():
            total = await session.scalar(select(func.count(Product.id))) or 0
            active = await session.scalar(select(func.count(Product.id)).where(Product.is_active == True)) or 0
            revenue_stmt = select(func.coalesce(func.sum(Payment.amount), 0))
            revenue = await session.scalar(revenue_stmt) or 0
            break

        lines = [
            "📊 <b>Статистика продуктов</b>",
            "",
            f"Всего продуктов: {total}",
            f"Активных: {active}",
            f"Выключенных: {total - active}",
            f"Сумма оплат (всего): {_format_currency(Decimal(revenue))}",
        ]

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_products")]]
        )

        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        await callback.answer()

    except Exception as exc:
        logger.exception("Error showing product stats", exc_info=exc)
        await callback.answer("❌ Ошибка при загрузке статистики", show_alert=True)

@router.callback_query(F.data == "manager_broadcasts")
@role_required(AdminRole.MANAGER)
async def show_manager_broadcasts(callback: CallbackQuery):
    """Show broadcast metrics overview for managers."""
    try:
        metrics: Dict[str, Any] = {}
        async for session in get_db():
            service = AnalyticsService(session)
            metrics = await service.get_broadcast_metrics()
            break

        if not metrics:
            await callback.answer("❌ Нет данных по рассылкам", show_alert=True)
            return

        lines = format_broadcast_metrics(metrics)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="manager_broadcasts")],
            [InlineKeyboardButton(text="⬅️ К аналитике", callback_data="admin_analytics")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")],
        ])

        await callback.message.edit_text(lines, reply_markup=keyboard, parse_mode="HTML")

    except Exception:
        logger.exception("Error showing broadcast metrics")
        await callback.answer("❌ Ошибка при загрузке рассылок", show_alert=True)


# Broadcast Management
@router.callback_query(F.data == "admin_broadcasts")
@role_required(AdminRole.EDITOR)
async def broadcast_management(callback: CallbackQuery):
    """Broadcast management menu."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆕 Новая рассылка", callback_data="broadcast_create")],
        [InlineKeyboardButton(text="📊 История рассылок", callback_data="broadcast_history")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(
        "📢 <b>Управление рассылками</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "broadcast_create")
@role_required(AdminRole.EDITOR)
async def broadcast_create(callback: CallbackQuery, state: FSMContext):
    """Start creating new broadcast."""
    await state.set_state(AdminStates.waiting_for_broadcast_content)
    await state.update_data(
        broadcast_items=[],
        selected_segment=None,
        broadcast_summary_message_id=None,
        scheduled_for_iso=None,
        scheduled_for_display=None,
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcasts")],
        ]
    )

    await callback.message.edit_text(
        "📝 <b>Новая рассылка</b>\n\n"
        "Шаг 1/4: отправьте одно или несколько сообщений, которые должны попасть в рассылку.\n\n"
        "Можно прикреплять текст, изображения, видео, документы, аудио и голосовые сообщения — в любом количестве."
        " Когда добавите все материалы, появится кнопка «➡️ Выбрать аудиторию».",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    seller_logger.info(
        "broadcast.create.started",
        admin_id=callback.from_user.id,
    )


@router.message(AdminStates.waiting_for_broadcast_content)
@role_required(AdminRole.EDITOR)
async def broadcast_content_received(message: Message, state: FSMContext):
    """Collect broadcast content items from admin messages."""
    seller_logger.info(
        "broadcast.content.received",
        admin_id=message.from_user.id,
        message_id=message.message_id,
    )

    try:
        new_items = _extract_broadcast_items(message)
    except ValueError:
        await message.answer(
            "❌ Этот тип сообщения пока не поддерживается в рассылках."
        )
        return

    data = await state.get_data()
    items: List[Dict[str, Any]] = data.get("broadcast_items", [])
    items.extend(new_items)
    summary_message_id = data.get("broadcast_summary_message_id")

    counts = Counter(item.get("type") for item in items)
    summary_parts = [
        f"{label}: {count}"
        for label, count in counts.items()
    ]
    summary = ", ".join(summary_parts)

    preview_text = next(
        (
            (item.get("plain_text") or "").strip()
            for item in items
            if item.get("type") == "text" and item.get("plain_text")
        ),
        "",
    )
    if not preview_text:
        preview_text = next(
            (
                (item.get("plain_caption") or "").strip()
                for item in items
                if item.get("plain_caption")
            ),
            "",
        )

    preview_display = (preview_text or "—").strip() or "—"
    if len(preview_display) > 200:
        preview_display = preview_display[:200] + "..."

    summary_text = (
        "✅ <b>Материалы для рассылки обновлены</b>\n"
        f"Сейчас элементов: {len(items)}.\n"
    )
    if summary:
        summary_text += f"📎 Состав: {summary}\n"
    summary_text += (
        f"📝 Предпросмотр текста: {escape(preview_display)}\n\n"
        "Когда закончите добавлять материалы, нажмите «➡️ Выбрать аудиторию»."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Выбрать аудиторию", callback_data="broadcast_choose_segment")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcasts")],
        ]
    )

    summary_message = None
    if summary_message_id:
        try:
            await message.bot.edit_message_text(
                summary_text,
                chat_id=message.chat.id,
                message_id=summary_message_id,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except TelegramBadRequest:
            summary_message = await message.answer(
                summary_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
    if summary_message is None and not summary_message_id:
        summary_message = await message.answer(
            summary_text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    if summary_message:
        summary_message_id = summary_message.message_id

    await state.update_data(
        broadcast_items=items,
        broadcast_summary_message_id=summary_message_id,
    )
    seller_logger.info(
        "broadcast.content.stored",
        admin_id=message.from_user.id,
        total_items=len(items),
    )


@router.callback_query(F.data == "broadcast_choose_segment")
@role_required(AdminRole.EDITOR)
async def broadcast_choose_segment(callback: CallbackQuery, state: FSMContext):
    """Move to segment selection after content preparation."""
    data = await state.get_data()
    items: List[Dict[str, Any]] = data.get("broadcast_items", [])

    if not items:
        await callback.answer("Сначала добавьте хотя бы одно сообщение", show_alert=True)
        seller_logger.info(
            "broadcast.segment.denied",
            admin_id=callback.from_user.id,
            reason="no_items",
        )
        return

    await state.update_data(
        broadcast_summary_message_id=None,
        scheduled_for_iso=None,
        scheduled_for_display=None,
    )

    counts = Counter(item.get("type") for item in items)
    summary_parts = [f"{label}: {count}" for label, count in counts.items()]
    summary = ", ".join(summary_parts)

    preview_text = next(
        (
            (item.get("plain_text") or "").strip()
            for item in items
            if item.get("type") == "text" and item.get("plain_text")
        ),
        "",
    )
    if not preview_text:
        preview_text = next(
            (
                (item.get("plain_caption") or "").strip()
                for item in items
                if item.get("plain_caption")
            ),
            "",
        )

    preview_text = preview_text or "—"
    if len(preview_text) > 200:
        preview_text = preview_text[:200] + "..."

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Все пользователи", callback_data="broadcast_all")],
            [InlineKeyboardButton(text="❄️ Холодные", callback_data="broadcast_cold")],
            [InlineKeyboardButton(text="🔥 Тёплые", callback_data="broadcast_warm")],
            [InlineKeyboardButton(text="🌶️ Горячие", callback_data="broadcast_hot")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcasts")],
        ]
    )

    await state.set_state(AdminStates.waiting_for_broadcast_segment)
    await callback.message.edit_text(
        "📦 <b>Материалы собраны</b>\n\n"
        f"📝 Предпросмотр текста: {escape(preview_text)}\n"
        + (f"📎 Вложения: {summary}\n\n" if summary else "\n")
        + "🎯 <b>Шаг 2/4:</b> Выберите целевую аудиторию:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    seller_logger.info(
        "broadcast.segment.selection_started",
        admin_id=callback.from_user.id,
        total_items=len(items),
    )


@router.callback_query(F.data.in_({"broadcast_all", "broadcast_cold", "broadcast_warm", "broadcast_hot"}))
@role_required(AdminRole.EDITOR)
async def broadcast_segment_selected(callback: CallbackQuery, state: FSMContext):
    """Show preview for the selected segment before sending."""
    try:
        segment = callback.data.split("_")[1]
        data = await state.get_data()
        items: List[Dict[str, Any]] = data.get("broadcast_items", [])

        if not items:
            await callback.answer("❌ Материалы рассылки не найдены", show_alert=True)
            seller_logger.warning(
                "broadcast.segment.no_materials",
                admin_id=callback.from_user.id,
                segment=segment,
            )
            await state.set_state(AdminStates.waiting_for_broadcast_content)
            return

        items = list(items)
        await state.update_data(
            broadcast_items=items,
            selected_segment=segment,
            scheduled_for_iso=None,
            scheduled_for_display=None,
        )
        await state.set_state(AdminStates.waiting_for_broadcast_schedule)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Изменить аудиторию", callback_data="broadcast_choose_segment")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcasts")],
            ]
        )

        await callback.message.edit_text(
            "🗓 <b>Планирование рассылки</b>\n\n"
            "Шаг 3/4: отправьте дату и время публикации в формате <code>01.01.2025 17:00</code>.\n"
            "Время указывается по Москве (UTC+3). После ввода пришлю кнопку «➡️ Продолжить».",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        await callback.answer()
        seller_logger.info(
            "broadcast.schedule.requested",
            admin_id=callback.from_user.id,
            segment=segment,
            total_items=len(items),
        )

    except Exception as e:
        logger.error(f"Error sending broadcast: {e}")
        await callback.answer("❌ Ошибка при отправке рассылки", show_alert=True)
        await state.clear()


@router.message(AdminStates.waiting_for_broadcast_schedule)
@role_required(AdminRole.EDITOR)
async def broadcast_schedule_received(message: Message, state: FSMContext):
    """Receive and validate the scheduled send time from admin."""
    raw_text = (message.text or "").strip()
    if not raw_text:
        await message.answer(
            "❌ Укажите дату и время в формате <code>01.01.2025 17:00</code> (Москва).",
            parse_mode="HTML",
        )
        return

    try:
        scheduled_naive = datetime.strptime(raw_text, "%d.%m.%Y %H:%M")
    except ValueError:
        await message.answer(
            "❌ Неверный формат. Используйте <code>01.01.2025 17:00</code>.",
            parse_mode="HTML",
        )
        return

    scheduled_local = scheduled_naive.replace(tzinfo=MOSCOW_TZ)
    now_local = datetime.now(MOSCOW_TZ)
    if scheduled_local <= now_local:
        await message.answer(
            "❌ Укажите дату и время в будущем (Москва).",
            parse_mode="HTML",
        )
        return

    scheduled_utc = scheduled_local.astimezone(timezone.utc)
    scheduled_display = scheduled_local.strftime("%d.%m.%Y %H:%M")

    await state.update_data(
        scheduled_for_iso=scheduled_utc.isoformat(),
        scheduled_for_display=scheduled_display,
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➡️ Продолжить", callback_data="broadcast_schedule_continue")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcasts")],
        ]
    )

    await message.answer(
        "✅ Рассылка сохранена и будет опубликована в указанное время.\n"
        f"🗓 {escape(scheduled_display)} (Мск)\n\n"
        "Нажмите «➡️ Продолжить», чтобы перейти к предпросмотру.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    seller_logger.info(
        "broadcast.schedule.saved",
        admin_id=message.from_user.id,
        scheduled_for=scheduled_utc.isoformat(),
    )


async def _present_broadcast_preview(callback: CallbackQuery, state: FSMContext) -> None:
    """Send preview of the broadcast content and show confirmation controls."""
    data = await state.get_data()
    items: List[Dict[str, Any]] = data.get("broadcast_items", [])
    segment = data.get("selected_segment")
    scheduled_display = data.get("scheduled_for_display")

    if not items or not segment:
        await callback.answer("❌ Не удалось сформировать предпросмотр", show_alert=True)
        seller_logger.warning(
            "broadcast.preview.missing_data",
            admin_id=callback.from_user.id,
            has_items=bool(items),
            segment=segment,
        )
        await state.set_state(AdminStates.waiting_for_broadcast_content)
        await state.update_data(
            selected_segment=None,
            scheduled_for_iso=None,
            scheduled_for_display=None,
        )
        return

    await callback.message.edit_text(
        "📋 Формируем предпросмотр…",
        parse_mode="HTML",
    )

    try:
        await _send_preview_items(callback.bot, callback.message.chat.id, items)
    except Exception:
        await callback.message.answer(
            "❌ Не удалось показать предпросмотр. Попробуйте ещё раз или измените материалы.",
            parse_mode="HTML",
        )
        await state.set_state(AdminStates.waiting_for_broadcast_content)
        await state.update_data(
            selected_segment=None,
            scheduled_for_iso=None,
            scheduled_for_display=None,
        )
        await callback.answer()
        return

    counts = Counter(item.get("type") for item in items)
    summary_parts = [f"{label}: {count}" for label, count in counts.items()]
    summary = ", ".join(summary_parts)

    segment_names = {
        "all": "👥 Все пользователи",
        "cold": "❄️ Холодные",
        "warm": "🔥 Тёплые",
        "hot": "🌶️ Горячие",
    }

    summary_message = (
        "📋 <b>Предпросмотр готов</b>\n\n"
        f"🎯 Аудитория: {segment_names.get(segment, segment)}"
    )
    if scheduled_display:
        summary_message += f"\n🗓 Отправка: {escape(scheduled_display)} (Мск)"
    if summary:
        summary_message += f"\n📎 Материалы: {summary}"
    summary_message += "\n\n📌 Предпросмотр отправлен только вам."

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Отправить", callback_data="broadcast_confirm_send")],
            [InlineKeyboardButton(text="✏️ Редактировать", callback_data="broadcast_edit")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcasts")],
        ]
    )

    await callback.message.edit_text(
        summary_message + "\n\n🚀 <b>Шаг 4/4:</b> Отправить рассылку?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()
    seller_logger.info(
        "broadcast.preview.presented",
        admin_id=callback.from_user.id,
        segment=segment,
        total_items=len(items),
        scheduled_for=scheduled_display,
    )


@router.callback_query(F.data == "broadcast_schedule_continue")
@role_required(AdminRole.EDITOR)
async def broadcast_schedule_continue(callback: CallbackQuery, state: FSMContext):
    """Move to preview after schedule confirmation."""
    data = await state.get_data()
    if not data.get("scheduled_for_iso"):
        await callback.answer("Сначала укажите дату и время отправки", show_alert=True)
        return

    await state.set_state(AdminStates.waiting_for_broadcast_confirmation)
    await _present_broadcast_preview(callback, state)


@router.callback_query(F.data == "broadcast_edit")
@role_required(AdminRole.EDITOR)
async def broadcast_edit(callback: CallbackQuery, state: FSMContext):
    """Return to content collection for editing."""
    data = await state.get_data()
    previous_count = len(data.get("broadcast_items", []))

    await state.set_state(AdminStates.waiting_for_broadcast_content)
    await state.update_data(
        broadcast_items=[],
        selected_segment=None,
        broadcast_summary_message_id=None,
        scheduled_for_iso=None,
        scheduled_for_display=None,
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_broadcasts")],
        ]
    )

    await callback.message.edit_text(
        "✏️ <b>Редактирование рассылки</b>\n\n"
        "Все предыдущие материалы удалены. Отправьте новые сообщения и вложения — после этого появится кнопка выбора аудитории.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    await callback.answer()
    seller_logger.info(
        "broadcast.edit.reset",
        admin_id=callback.from_user.id,
        removed_items=previous_count,
    )


@router.callback_query(F.data == "broadcast_confirm_send")
@role_required(AdminRole.EDITOR)
async def broadcast_confirm_send(callback: CallbackQuery, state: FSMContext):
    """Finalize and send the broadcast to selected segment."""
    data = await state.get_data()
    items: List[Dict[str, Any]] = data.get("broadcast_items", [])
    segment = data.get("selected_segment")
    scheduled_iso = data.get("scheduled_for_iso")
    scheduled_display = data.get("scheduled_for_display")

    if not items or not segment:
        await callback.answer("❌ Не удалось отправить: нет данных рассылки", show_alert=True)
        seller_logger.error(
            "broadcast.send.missing_data",
            admin_id=callback.from_user.id,
            has_items=bool(items),
            segment=segment,
        )
        await state.clear()
        return

    if not scheduled_iso or not scheduled_display:
        await callback.answer("❌ Укажите дату и время отправки перед подтверждением", show_alert=True)
        seller_logger.error(
            "broadcast.send.missing_schedule",
            admin_id=callback.from_user.id,
            segment=segment,
        )
        await state.clear()
        return

    scheduled_at = datetime.fromisoformat(scheduled_iso)
    now_utc = datetime.now(timezone.utc)
    immediate_send = scheduled_at <= now_utc + timedelta(minutes=1)

    seller_logger.info(
        "broadcast.send.started",
        admin_id=callback.from_user.id,
        segment=segment,
        total_items=len(items),
        scheduled_for=scheduled_iso,
        immediate=immediate_send,
    )

    text_preview = next(
        (
            (item.get("plain_text") or "").strip()
            for item in items
            if item.get("type") == "text" and item.get("plain_text")
        ),
        "",
    )
    if not text_preview:
        text_preview = next(
            (
                (item.get("plain_caption") or "").strip()
                for item in items
                if item.get("plain_caption")
            ),
            "",
        )

    body_preview = text_preview or ""

    segment_filter = None
    if segment != "all":
        segment_map = {
            "cold": "COLD",
            "warm": "WARM",
            "hot": "HOT",
        }
        segment_filter = {"segments": [segment_map.get(segment, segment.upper())]}

    try:
        from app.services.broadcast_service import BroadcastService
        from app.db import get_db

        send_result: Dict[str, Any] = {}
        job_id: Optional[str] = None

        async for session in get_db():
            broadcast_service = BroadcastService(callback.bot, session)
            broadcast = await broadcast_service.create_simple_broadcast(
                title=f"Рассылка {datetime.now().strftime('%d.%m.%Y')}",
                body=body_preview,
                segment_filter=segment_filter,
                content=items,
            )

            await session.commit()

            if immediate_send:
                send_result = await broadcast_service.send_simple_broadcast(broadcast.id)
                await session.commit()
            else:
                job_id = await scheduler_service.schedule_broadcast(broadcast.id, scheduled_at)
                send_result = {"job_id": job_id}

            break

        segment_names = {
            "all": "👥 Все пользователи",
            "cold": "❄️ Холодные",
            "warm": "🔥 Тёплые",
            "hot": "🌶️ Горячие",
        }

        preview_display = text_preview or "—"
        if len(preview_display) > 100:
            preview_display = preview_display[:100] + "..."

        counts = Counter(item.get("type") for item in items)
        summary_parts = [f"{label}: {count}" for label, count in counts.items()]
        summary = ", ".join(summary_parts)

        if immediate_send:
            sent = send_result.get("sent", 0)
            failed = send_result.get("failed", 0)
            total = send_result.get("total", 0)

            await callback.message.edit_text(
                f"✅ <b>Рассылка отправлена!</b>\n\n"
                f"📝 Текст: {escape(preview_display)}\n"
                + (f"📎 Материалы: {summary}\n" if summary else "")
                + f"🎯 Аудитория: {segment_names.get(segment, segment)}\n"
                + f"🗓 План: {escape(scheduled_display)} (Мск)\n"
                + f"📊 Результат: {sent} отправлено, {failed} ошибок из {total}\n"
                + f"📅 Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                parse_mode="HTML",
            )
            await callback.answer("Рассылка запущена")
            await state.clear()
            seller_logger.info(
                "broadcast.send.completed",
                admin_id=callback.from_user.id,
                segment=segment,
                scheduled_for=scheduled_iso,
                sent=sent,
                failed=failed,
                total=total,
            )
        else:
            await callback.message.edit_text(
                f"✅ <b>Рассылка запланирована!</b>\n\n"
                f"📝 Текст: {escape(preview_display)}\n"
                + (f"📎 Материалы: {summary}\n" if summary else "")
                + f"🎯 Аудитория: {segment_names.get(segment, segment)}\n"
                + f"🗓 Отправка: {escape(scheduled_display)} (Мск)\n",
                parse_mode="HTML",
            )
            await callback.answer("Рассылка запланирована")
            await state.clear()
            seller_logger.info(
                "broadcast.send.scheduled",
                admin_id=callback.from_user.id,
                segment=segment,
                scheduled_for=scheduled_iso,
                scheduler_job_id=job_id,
            )

    except Exception as exc:
        logger.exception("Error sending broadcast", exc_info=exc)
        await callback.answer("❌ Ошибка при запуске рассылки", show_alert=True)
        seller_logger.error(
            "broadcast.send.failed",
            admin_id=callback.from_user.id,
            segment=segment,
            error=str(exc),
        )
        await state.clear()

# Bonus Management
@router.callback_query(F.data == "admin_bonus")
@role_required(AdminRole.EDITOR)
async def admin_bonus_menu(callback: CallbackQuery, state: FSMContext):
    """Entry point for managing bonus materials."""
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Начать", callback_data="admin_bonus_start")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")],
    ])
    await callback.message.edit_text(
        "🎁 <b>Бонусный материал</b>\n\nЗдесь Вы можете изменить бонус-файл и описание",
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    logger.info("Admin %s opened bonus management", callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "admin_bonus_start")
@role_required(AdminRole.EDITOR)
async def admin_bonus_start(callback: CallbackQuery, state: FSMContext):
    """Ask admin to upload a new bonus file."""
    await state.set_state(AdminStates.waiting_for_bonus_file)
    await state.update_data(pending_bonus_file=None, pending_bonus_caption=None)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")],
    ])
    await callback.message.edit_text(
        "Загрузите сюда новый файл, он будет отправляться в качестве нового бонусного файла",
        reply_markup=keyboard,
    )
    logger.info("Admin %s started bonus file upload", callback.from_user.id)
    await callback.answer()


@router.message(AdminStates.waiting_for_bonus_file)
@role_required(AdminRole.EDITOR)
async def admin_bonus_file_received(message: Message, state: FSMContext):
    """Handle bonus file upload from admin."""
    document = message.document
    if not document:
        await message.answer("Пожалуйста, отправьте файл в формате PDF.")
        logger.warning("Admin %s sent non-document while bonus file awaited", message.from_user.id)
        return

    filename = (document.file_name or "").strip()
    if not filename.lower().endswith(".pdf"):
        await message.answer("Поддерживаются только PDF-файлы. Отправьте корректный файл.")
        logger.warning("Admin %s attempted non-pdf bonus file %s", message.from_user.id, filename)
        return

    target_path = BonusContentManager.target_path(filename)
    try:
        await message.bot.download(document, destination=target_path)
    except Exception as exc:  # pragma: no cover - network/filesystem guard
        logger.exception(
            "Failed to store bonus file %s for admin %s: %s",
            filename,
            message.from_user.id,
            exc,
        )
        await message.answer("❌ Не удалось сохранить файл. Попробуйте снова.")
        return

    data = await state.get_data()
    existing_caption = data.get("pending_bonus_caption")

    await state.update_data(pending_bonus_file=filename)
    await state.set_state(AdminStates.waiting_for_bonus_description)

    if existing_caption:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Предпросмотр", callback_data="admin_bonus_preview")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")],
        ])
        await message.answer(
            "Файл сохранён. Можно открыть «Предпросмотр» или отправить новое описание.",
            reply_markup=keyboard,
        )
        logger.info(
            "Admin %s replaced bonus file at %s keeping caption length=%d",
            message.from_user.id,
            target_path,
            len(existing_caption),
        )
    else:
        await message.answer("Файл сохранён. Напишите описание для этого файла, которое увидят пользователи.")
        logger.info("Admin %s uploaded new bonus file saved to %s", message.from_user.id, target_path)


@router.message(AdminStates.waiting_for_bonus_description)
@role_required(AdminRole.EDITOR)
async def admin_bonus_description_received(message: Message, state: FSMContext):
    """Store bonus description text provided by admin."""
    caption = (message.text or "").strip()
    if not caption:
        await message.answer("Описание не может быть пустым. Введите текст ещё раз.")
        logger.warning("Admin %s submitted empty bonus description", message.from_user.id)
        return

    await state.update_data(pending_bonus_caption=caption)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Предпросмотр", callback_data="admin_bonus_preview")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")],
    ])
    await message.answer(
        "Описание сохранено. Нажмите «Предпросмотр», чтобы увидеть файл так, как его получат пользователи.",
        reply_markup=keyboard,
    )
    logger.info("Admin %s provided bonus description length=%d", message.from_user.id, len(caption))


@router.callback_query(F.data == "admin_bonus_preview")
@role_required(AdminRole.EDITOR)
async def admin_bonus_preview(callback: CallbackQuery, state: FSMContext):
    """Send preview of the new bonus file with caption."""
    data = await state.get_data()
    filename = data.get("pending_bonus_file")
    caption = data.get("pending_bonus_caption")

    if not filename or not caption:
        await callback.answer("Сначала загрузите файл и описание.", show_alert=True)
        logger.warning("Admin %s requested bonus preview without data", callback.from_user.id)
        return

    file_path = BonusContentManager.ensure_storage() / filename
    if not file_path.exists():
        await callback.answer("Файл не найден. Загрузите его снова.", show_alert=True)
        logger.warning("Admin %s preview missing file at %s", callback.from_user.id, file_path)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Сохранить и опубликовать", callback_data="admin_bonus_publish")],
        [InlineKeyboardButton(text="Редактировать файл", callback_data="admin_bonus_edit_file")],
        [InlineKeyboardButton(text="Редактировать подпись", callback_data="admin_bonus_edit_caption")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")],
    ])

    await callback.message.answer_document(
        FSInputFile(file_path),
        caption=caption,
        reply_markup=keyboard,
    )
    logger.info(
        "Admin %s previewed bonus content file=%s caption_length=%d",
        callback.from_user.id,
        filename,
        len(caption),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_bonus_edit_file")
@role_required(AdminRole.EDITOR)
async def admin_bonus_edit_file(callback: CallbackQuery, state: FSMContext):
    """Allow admin to re-upload bonus file."""
    await state.set_state(AdminStates.waiting_for_bonus_file)
    await callback.message.answer(
        "Загрузите сюда новый файл, он будет отправляться в качестве нового бонусного файла",
    )
    logger.info("Admin %s requested bonus file re-upload", callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "admin_bonus_edit_caption")
@role_required(AdminRole.EDITOR)
async def admin_bonus_edit_caption(callback: CallbackQuery, state: FSMContext):
    """Allow admin to update bonus caption."""
    await state.set_state(AdminStates.waiting_for_bonus_description)
    await callback.message.answer("Напишите описание для этого файла, которое увидят пользователи.")
    logger.info("Admin %s requested bonus caption re-edit", callback.from_user.id)
    await callback.answer()


@router.callback_query(F.data == "admin_bonus_publish")
@role_required(AdminRole.EDITOR)
async def admin_bonus_publish(callback: CallbackQuery, state: FSMContext):
    """Persist bonus changes and publish them for users."""
    data = await state.get_data()
    filename = data.get("pending_bonus_file")
    caption = data.get("pending_bonus_caption")

    if not filename or not caption:
        await callback.answer("Нет данных для сохранения. Загрузите файл и описание.", show_alert=True)
        logger.warning("Admin %s attempted to publish bonus without data", callback.from_user.id)
        return

    BonusContentManager.persist_metadata(filename, caption)
    await state.clear()

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")],
        [InlineKeyboardButton(text="🎁 Настроить снова", callback_data="admin_bonus")],
    ])

    await callback.message.answer(
        "✅ Новый бонус сохранён и будет показан пользователям.",
        reply_markup=keyboard,
    )
    logger.info(
        "Admin %s published bonus file=%s caption_length=%d",
        callback.from_user.id,
        filename,
        len(caption),
    )
    await callback.answer("Готово!")


# Leads Management
@router.callback_query(F.data == "admin_leads")
@admin_required
async def leads_management(callback: CallbackQuery):
    """Leads management menu."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Новые лиды", callback_data="leads_new")],
        [InlineKeyboardButton(text="🔄 В работе", callback_data="leads_in_progress")],
        [InlineKeyboardButton(text="✅ Завершённые", callback_data="leads_completed")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(
        "👥 <b>Управление лидами</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("leads_"))
@admin_required
async def show_leads(callback: CallbackQuery):
    """Show leads by status."""
    try:
        status = callback.data.split("_")[1]
        
        async for session in get_db():
            from app.models import Lead, LeadStatus
            from app.repositories.user_repository import UserRepository
            
            # Map status strings to enum values
            status_map = {
                "new": LeadStatus.NEW,
                "in": LeadStatus.TAKEN,
                "progress": LeadStatus.TAKEN,  # "in_progress" -> "in"
                "completed": LeadStatus.DONE
            }
            
            lead_status = status_map.get(status, LeadStatus.NEW)
            
            # Get leads with user info
            stmt = select(Lead, User.first_name, User.last_name, User.username).join(User).where(
                Lead.status == lead_status
            ).order_by(Lead.created_at.desc()).limit(10)
            
            result = await session.execute(stmt)
            leads_data = result.all()
            break
        
        status_names = {
            "new": "👥 Новые лиды",
            "in": "🔄 Лиды в работе", 
            "progress": "🔄 Лиды в работе",
            "completed": "✅ Завершённые лиды"
        }
        
        if not leads_data:
            text = f"{status_names.get(status, 'Лиды')}\n\n📭 Нет лидов в данной категории."
        else:
            text = f"{status_names.get(status, 'Лиды')}\n\n"
            
            for i, (lead, first_name, last_name, username) in enumerate(leads_data, 1):
                name = f"{first_name or ''} {last_name or ''}" or f"@{username}" or f"ID {lead.user_id}"
                created = lead.created_at.strftime('%d.%m %H:%M')
                
                text += f"{i}. {name}\n"
                text += f"   📅 {created} | 💯 Скор: {lead.user.lead_score if hasattr(lead, 'user') else 'N/A'}\n\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"leads_{status}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_leads")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing leads: {e}")
        await callback.answer("❌ Ошибка при загрузке лидов", show_alert=True)


# User Management
@router.callback_query(F.data == "admin_users")
@role_required(AdminRole.ADMIN)
async def users_management(callback: CallbackQuery):
    """User management menu."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика пользователей", callback_data="users_stats")],
        [InlineKeyboardButton(text="🔍 Поиск пользователя", callback_data="users_search")],
        [InlineKeyboardButton(text="👥 Последние регистрации", callback_data="users_recent")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ])

    await callback.message.edit_text(
        "👤 <b>Управление пользователями</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "users_search")
@role_required(AdminRole.ADMIN)
async def users_search(callback: CallbackQuery):
    """Placeholder for user search functionality."""
    logger.info(
        "users_search callback triggered by user_id=%s - feature not configured",
        callback.from_user.id,
    )
    await callback.answer()
    await callback.message.answer("Функция не настроена")


@router.callback_query(F.data == "users_stats")
@role_required(AdminRole.ADMIN)
async def users_stats(callback: CallbackQuery):
    """Show detailed user statistics."""
    try:
        async for session in get_db():
            # Registration stats by day
            today = datetime.now(timezone.utc).date()
            week_ago = today - timedelta(days=7)
            month_ago = today - timedelta(days=30)
            
            today_users = await session.execute(
                select(func.count(User.id)).where(
                    func.date(User.created_at) == today
                )
            )
            today_count = today_users.scalar()
            
            week_users = await session.execute(
                select(func.count(User.id)).where(
                    func.date(User.created_at) >= week_ago
                )
            )
            week_count = week_users.scalar()
            
            month_users = await session.execute(
                select(func.count(User.id)).where(
                    func.date(User.created_at) >= month_ago
                )
            )
            month_count = month_users.scalar()
            
            # Survey completion rates
            survey_completed = await session.execute(
                select(func.count(User.id)).where(User.survey_completed_at.isnot(None))
            )
            survey_count = survey_completed.scalar()
            
            total_users = await session.execute(select(func.count(User.id)))
            total_count = total_users.scalar()
            
            break
        
        completion_rate = (survey_count / max(total_count, 1)) * 100
        
        stats_text = f"""📊 <b>Подробная статистика пользователей</b>

📅 <b>Регистрации:</b>
• Сегодня: {today_count}
• За неделю: {week_count}
• За месяц: {month_count}

📝 <b>Анкеты:</b>
• Прошли анкету: {survey_count}
• Конверсия: {completion_rate:.1f}%

💯 <b>Общие показатели:</b>
• Всего пользователей: {total_count}"""
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="users_stats")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users")]
        ])
        
        await callback.message.edit_text(
            stats_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Error showing user stats: {e}")
        await callback.answer("❌ Ошибка при загрузке статистики", show_alert=True)


@router.callback_query(F.data == "users_recent")
@role_required(AdminRole.ADMIN)
async def users_recent(callback: CallbackQuery):
    """Show recent user registrations."""
    try:
        async for session in get_db():
            stmt = select(User).order_by(User.created_at.desc()).limit(10)
            result = await session.execute(stmt)
            recent_users = result.scalars().all()
            break
        
        if not recent_users:
            text = "👥 <b>Последние регистрации</b>\n\n📍 Нет новых пользователей."
        else:
            text = "👥 <b>Последние регистрации</b>\n\n"
            
            for i, user in enumerate(recent_users, 1):
                name = f"{user.first_name or ''} {user.last_name or ''}" or f"@{user.username}" or f"ID {user.id}"
                segment = user.segment or "не определен"
                created = user.created_at.strftime('%d.%m %H:%M')
                
                text += f"{i}. {name}\n"
                text += f"   🎯 {segment} | 💯 {user.lead_score} | 📅 {created}\n\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="users_recent")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_users")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing recent users: {e}")
        await callback.answer("❌ Ошибка при загрузке", show_alert=True)


# Payment Management
@router.callback_query(F.data == "admin_payments")
@role_required(AdminRole.ADMIN)
async def payments_management(callback: CallbackQuery):
    """Payment management menu."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Последние платежи", callback_data="payments_recent")],
        [InlineKeyboardButton(text="📊 Статистика платежей", callback_data="payments_stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(
        "💳 <b>Управление платежами</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "payments_recent")
@role_required(AdminRole.ADMIN)
async def payments_recent(callback: CallbackQuery):
    """Show recent payments."""
    try:
        async for session in get_db():
            stmt = select(Payment, User.first_name, User.last_name, User.username).join(
                User, Payment.user_id == User.id
            ).order_by(Payment.created_at.desc()).limit(10)
            
            result = await session.execute(stmt)
            payments_data = result.all()
            break
        
        if not payments_data:
            text = "💰 <b>Последние платежи</b>\n\n📭 Нет платежей."
        else:
            text = "💰 <b>Последние платежи</b>\n\n"
            
            for i, (payment, first_name, last_name, username) in enumerate(payments_data, 1):
                name = f"{first_name or ''} {last_name or ''}" or f"@{username}" or f"ID {payment.user_id}"
                created = payment.created_at.strftime('%d.%m %H:%M')
                status_emoji = "✅" if payment.status == "paid" else "⏳" if payment.status == "pending" else "❌"
                
                text += f"{i}. {name}\n"
                text += f"   💰 {payment.amount:,.0f} ₽ | {status_emoji} {payment.status} | 📅 {created}\n\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="payments_recent")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_payments")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing recent payments: {e}")
        await callback.answer("❌ Ошибка при загрузке платежей", show_alert=True)


@router.callback_query(F.data == "payments_stats")
@role_required(AdminRole.ADMIN)
async def payments_stats(callback: CallbackQuery):
    """Show payment statistics."""
    try:
        async for session in get_db():
            # Payment stats by period
            today = datetime.now(timezone.utc).date()
            week_ago = today - timedelta(days=7)
            month_ago = today - timedelta(days=30)
            
            today_payments = await session.execute(
                select(func.count(Payment.id), func.sum(Payment.amount)).where(
                    func.date(Payment.created_at) == today,
                    Payment.status == "paid"
                )
            )
            today_count, today_amount = today_payments.first()
            
            week_payments = await session.execute(
                select(func.count(Payment.id), func.sum(Payment.amount)).where(
                    func.date(Payment.created_at) >= week_ago,
                    Payment.status == "paid"
                )
            )
            week_count, week_amount = week_payments.first()
            
            month_payments = await session.execute(
                select(func.count(Payment.id), func.sum(Payment.amount)).where(
                    func.date(Payment.created_at) >= month_ago,
                    Payment.status == "paid"
                )
            )
            month_count, month_amount = month_payments.first()
            
            # Payment status distribution
            paid_count = await session.execute(
                select(func.count(Payment.id)).where(Payment.status == "paid")
            )
            paid = paid_count.scalar()
            
            pending_count = await session.execute(
                select(func.count(Payment.id)).where(Payment.status == "pending")
            )
            pending = pending_count.scalar()
            
            failed_count = await session.execute(
                select(func.count(Payment.id)).where(Payment.status == "failed")
            )
            failed = failed_count.scalar()
            
            break
        
        stats_text = f"""📊 <b>Статистика платежей</b>

📅 <b>Успешные платежи:</b>
• Сегодня: {today_count or 0} шт., {today_amount or 0:,.0f} ₽
• За неделю: {week_count or 0} шт., {week_amount or 0:,.0f} ₽
• За месяц: {month_count or 0} шт., {month_amount or 0:,.0f} ₽

📈 <b>Статусы платежей:</b>
• ✅ Оплачено: {paid}
• ⏳ В ожидании: {pending}
• ❌ Отклонено: {failed}"""
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="payments_stats")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_payments")]
        ])
        
        await callback.message.edit_text(
            stats_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Error showing payment stats: {e}")
        await callback.answer("❌ Ошибка при загрузке статистики", show_alert=True)


# Admin Management
@router.callback_query(F.data == "admin_admins")
@role_required(AdminRole.OWNER)
async def admins_management(callback: CallbackQuery):
    """Admin management menu."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Список админов", callback_data="admins_list")],
        [InlineKeyboardButton(text="➕ Добавить админа", callback_data="admins_add")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back")]
    ])
    
    await callback.message.edit_text(
        "⚙️ <b>Управление администраторами</b>\n\n"
        "Выберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "admins_list")
@role_required(AdminRole.OWNER)
async def admins_list(callback: CallbackQuery):
    """Show list of administrators."""
    try:
        async for session in get_db():
            from app.models import Admin
            
            stmt = select(Admin, User.first_name, User.last_name, User.username).join(
                User, Admin.user_id == User.id
            ).order_by(Admin.role, Admin.created_at)
            
            result = await session.execute(stmt)
            admins_data = result.all()
            break
        
        if not admins_data:
            text = "👥 <b>Список администраторов</b>\n\n📭 Нет администраторов."
        else:
            text = "👥 <b>Список администраторов</b>\n\n"
            
            role_emojis = {
                "OWNER": "👑",
                "ADMIN": "👨‍💼",
                "EDITOR": "✏️",
                "VIEWER": "👀"
            }
            
            for i, (admin, first_name, last_name, username) in enumerate(admins_data, 1):
                name = f"{first_name or ''} {last_name or ''}" or f"@{username}" or f"ID {admin.user_id}"
                role_emoji = role_emojis.get(admin.role.value, "👤")
                created = admin.created_at.strftime('%d.%m.%Y')
                
                text += f"{i}. {role_emoji} {admin.role.value}\n"
                text += f"   👤 {name}\n"
                text += f"   📅 С {created}\n\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="admins_list")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_admins")]
        ])
        
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error showing admins list: {e}")
        await callback.answer("❌ Ошибка при загрузке списка", show_alert=True)


@router.callback_query(F.data == "admins_add")
@role_required(AdminRole.OWNER)
async def admins_add(callback: CallbackQuery):
    """Show admin addition instructions."""
    text = """➕ <b>Добавление администратора</b>

📝 Для добавления нового администратора используйте команды:

<code>/add_admin [user_id] [role]</code>

👥 <b>Доступные роли:</b>
• <b>OWNER</b> - Полные права
• <b>ADMIN</b> - Управление пользователями и платежами
• <b>EDITOR</b> - Создание рассылок
• <b>VIEWER</b> - Только просмотр аналитики

📄 <b>Пример:</b>
<code>/add_admin 123456789 ADMIN</code>

📝 Для удаления:
<code>/remove_admin [user_id]</code>"""
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_admins")]
    ])
    
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")


@router.callback_query(F.data == "admin_back")
@admin_required
async def admin_back(callback: CallbackQuery, state: FSMContext):
    """Go back to admin panel."""
    await state.clear()
    await admin_panel(callback)


# Admin management commands
@router.message(Command("add_admin"))
@role_required(AdminRole.OWNER)
async def add_admin_command(message: Message):
    """Add new administrator."""
    try:
        parts = message.text.split()
        if len(parts) != 3:
            await message.answer(
                "❌ <b>Неверный формат команды</b>\n\n"
                "📝 Используйте: <code>/add_admin [user_id] [role]</code>\n\n"
                "🔹 Доступные роли: OWNER, ADMIN, EDITOR, VIEWER",
                parse_mode="HTML"
            )
            return
        
        user_id = int(parts[1])
        role_str = parts[2].upper()
        
        # Validate role
        valid_roles = ["OWNER", "ADMIN", "EDITOR", "VIEWER"]
        if role_str not in valid_roles:
            await message.answer(
                f"❌ <b>Неверная роль: {role_str}</b>\n\n"
                f"🔹 Доступные роли: {', '.join(valid_roles)}",
                parse_mode="HTML"
            )
            return
        
        async for session in get_db():
            admin_repo = AdminRepository(session)
            
            # Check if user exists
            user_stmt = select(User).where(User.id == user_id)
            user_result = await session.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            
            if not user:
                await message.answer(f"❌ Пользователь с ID {user_id} не найден")
                return
            
            # Check if already admin
            existing_admin = await admin_repo.get_admin_by_user_id(user_id)
            if existing_admin:
                await message.answer(
                    f"⚠️ Пользователь уже является администратором с ролью: {existing_admin.role.value}"
                )
                return
            
            # Add admin
            role_enum = AdminRole(role_str)
            new_admin = await admin_repo.create_admin(user_id, role_enum)
            
            await session.commit()
            break
        
        name = f"{user.first_name or ''} {user.last_name or ''}" or f"@{user.username}" or f"ID {user_id}"
        
        await message.answer(
            f"✅ <b>Администратор добавлен!</b>\n\n"
            f"👤 Пользователь: {name}\n"
            f"🎯 Роль: {role_str}\n"
            f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            parse_mode="HTML"
        )
        
    except ValueError:
        await message.answer("❌ User ID должен быть числом")
    except Exception as e:
        logger.error(f"Error adding admin: {e}")
        await message.answer("❌ Ошибка при добавлении администратора")


@router.message(Command("remove_admin"))
@role_required(AdminRole.OWNER)
async def remove_admin_command(message: Message):
    """Remove administrator."""
    try:
        parts = message.text.split()
        if len(parts) != 2:
            await message.answer(
                "❌ <b>Неверный формат команды</b>\n\n"
                "📝 Используйте: <code>/remove_admin [user_id]</code>",
                parse_mode="HTML"
            )
            return
        
        user_id = int(parts[1])
        
        # Prevent self-removal
        if user_id == message.from_user.id:
            await message.answer("❌ Нельзя удалить самого себя из администраторов")
            return
        
        async for session in get_db():
            admin_repo = AdminRepository(session)
            
            # Check if admin exists
            admin = await admin_repo.get_admin_by_user_id(user_id)
            if not admin:
                await message.answer(f"❌ Пользователь с ID {user_id} не является администратором")
                return
            
            # Get user info for confirmation
            user_stmt = select(User).where(User.id == user_id)
            user_result = await session.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            
            # Remove admin
            await admin_repo.remove_admin(user_id)
            await session.commit()
            break
        
        name = f"{user.first_name or ''} {user.last_name or ''}" if user else f"ID {user_id}"
        
        await message.answer(
            f"✅ <b>Администратор удален!</b>\n\n"
            f"👤 Пользователь: {name}\n"
            f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
            parse_mode="HTML"
        )
        
    except ValueError:
        await message.answer("❌ User ID должен быть числом")
    except Exception as e:
        logger.error(f"Error removing admin: {e}")
        await message.answer("❌ Ошибка при удалении администратора")


def register_full_admin_handlers(dp):
    """Register full admin handlers."""
    dp.include_router(router)


# --- Consultation Settings ---

CONSULTATION_SETTINGS_KEY = "consultation_settings"

async def _render_consultation_settings(message: Message, session):
    """Render the consultation settings panel."""
    repo = SystemSettingsRepository(session)
    settings = await repo.get_value(CONSULTATION_SETTINGS_KEY, default={})
    
    slots = settings.get("slots", ["12:00", "14:00", "16:00", "18:00"])
    cutoff_time = settings.get("cutoff_time", "17:45")
    reminder_offset = settings.get("reminder_offset", 15)

    text = (
        "📅 <b>Настройки консультаций</b>\n\n"
        f"<b>Текущие слоты (МСК):</b> {', '.join(slots)}\n"
        f"<b>Время среза для 'сегодня':</b> {cutoff_time} МСК\n"
        f"<b>Смещение напоминания:</b> за {reminder_offset} минут\n"
    )

    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="✏️ Изменить слоты", callback_data="consult_set:slots"))
    keyboard.add(InlineKeyboardButton(text="✏️ Изменить время среза", callback_data="consult_set:cutoff"))
    keyboard.add(InlineKeyboardButton(text="✏️ Изменить смещение", callback_data="consult_set:reminder"))
    keyboard.add(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_back"))
    keyboard.adjust(1)

    if isinstance(message, CallbackQuery):
        await message.message.edit_text(text, reply_markup=keyboard.as_markup(), parse_mode="HTML")
    else:
        await message.answer(text, reply_markup=keyboard.as_markup(), parse_mode="HTML")

@router.callback_query(F.data == "admin_consult_settings")
@role_required(AdminRole.ADMIN)
async def admin_consultation_settings(callback: CallbackQuery, **kwargs):
    """Show consultation settings."""
    async for session in get_db():
        await _render_consultation_settings(callback, session)
    await callback.answer()

@router.callback_query(F.data.startswith("consult_set:"))
@role_required(AdminRole.ADMIN)
async def edit_consultation_setting(callback: CallbackQuery, state: FSMContext):
    """Handle editing of a consultation setting."""
    setting = callback.data.split(":")[1]
    prompts = {
        "slots": ("Введите новые слоты времени через запятую (например, 12:00, 14:00, 18:00):", AdminStates.waiting_for_consultation_slots),
        "cutoff": ("Введите новое время среза в формате ЧЧ:ММ (например, 17:45):", AdminStates.waiting_for_cutoff_time),
        "reminder": ("Введите новое смещение для напоминания в минутах (например, 15):", AdminStates.waiting_for_reminder_offset),
    }
    if setting in prompts:
        prompt, new_state = prompts[setting]
        await state.set_state(new_state)
        await callback.message.answer(prompt)
        await callback.answer()

@router.message(AdminStates.waiting_for_consultation_slots)
@role_required(AdminRole.ADMIN)
async def set_consultation_slots(message: Message, state: FSMContext):
    """Set new consultation time slots."""
    slots = [s.strip() for s in message.text.split(",")]
    # Basic validation
    if not all(re.match(r"^\d{2}:\d{2}$", s) for s in slots):
        await message.answer("Неверный формат. Введите слоты через запятую, например: 12:00, 14:00")
        return
    
    async for session in get_db():
        repo = SystemSettingsRepository(session)
        settings = await repo.get_value(CONSULTATION_SETTINGS_KEY, default={})
        settings["slots"] = slots
        await repo.set_value(CONSULTATION_SETTINGS_KEY, settings)
        await session.commit()
        await _render_consultation_settings(message, session)
    await state.clear()

@router.message(AdminStates.waiting_for_cutoff_time)
@role_required(AdminRole.ADMIN)
async def set_cutoff_time(message: Message, state: FSMContext):
    """Set new cutoff time."""
    cutoff_time = message.text.strip()
    if not re.match(r"^\d{2}:\d{2}$", cutoff_time):
        await message.answer("Неверный формат. Введите время в формате ЧЧ:ММ, например: 17:45")
        return

    async for session in get_db():
        repo = SystemSettingsRepository(session)
        settings = await repo.get_value(CONSULTATION_SETTINGS_KEY, default={})
        settings["cutoff_time"] = cutoff_time
        await repo.set_value(CONSULTATION_SETTINGS_KEY, settings)
        await session.commit()
        await _render_consultation_settings(message, session)
    await state.clear()

@router.message(AdminStates.waiting_for_reminder_offset)
@role_required(AdminRole.ADMIN)
async def set_reminder_offset(message: Message, state: FSMContext):
    """Set new reminder offset."""
    try:
        reminder_offset = int(message.text.strip())
        if reminder_offset <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Неверный формат. Введите целое число минут (например, 15)")
        return

    async for session in get_db():
        repo = SystemSettingsRepository(session)
        settings = await repo.get_value(CONSULTATION_SETTINGS_KEY, default={})
        settings["reminder_offset"] = reminder_offset
        await repo.set_value(CONSULTATION_SETTINGS_KEY, settings)
        await session.commit()
        await _render_consultation_settings(message, session)
    await state.clear()


# --- SendTo Command ---

def _parse_usernames(text: str) -> List[str]:
    """Extract usernames from a string."""
    text = text.replace(",", " ").replace("\n", " ")
    raw_usernames = [part.strip() for part in text.split() if part.strip().startswith("@")]
    # Remove @ and duplicates, case-insensitive
    return sorted(list({uname[1:].lower() for uname in raw_usernames}))


@router.message(Command("sendto"))
@role_required(AdminRole.MANAGER)
async def sendto_command(message: Message, state: FSMContext):
    """Handle /sendto command to initiate a direct message to users."""
    args = message.text.split(maxsplit=1)
    if len(args) > 1:
        usernames = _parse_usernames(args[1])
        if not usernames:
            await message.answer("Не найдены получатели. Укажите @username получателей через пробел или запятую.")
            return

        if len(usernames) > settings.sendto_max_recipients:
            await message.answer(f"❌ Слишком много получателей. Максимум: {settings.sendto_max_recipients}.")
            return
        
        await state.update_data(sendto_recipients=usernames)
        await state.set_state(AdminStates.waiting_for_sendto_content)
        await message.answer(
            f"Ок, получатели: {len(usernames)}. Отправьте следующим сообщением текст/медиа для доставки.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Отмена", callback_data="sendto_cancel")]
            ])
        )
    else:
        await state.set_state(AdminStates.waiting_for_sendto_recipients)
        await message.answer(
            "Введите @username получателей (через пробел, запятую или с новой строки).",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Отмена", callback_data="sendto_cancel")]
            ])
        )


@router.message(AdminStates.waiting_for_sendto_recipients)
@role_required(AdminRole.MANAGER)
async def sendto_recipients_received(message: Message, state: FSMContext):
    """Handle recipients list for /sendto command."""
    if _is_cancel_text(message.text):
        await state.clear()
        await message.answer("❌ Отправка отменена.")
        return
        
    usernames = _parse_usernames(message.text)
    if not usernames:
        await message.answer("Не найдены получатели. Укажите @username получателей.")
        return

    if len(usernames) > settings.sendto_max_recipients:
        await message.answer(f"❌ Слишком много получателей. Максимум: {settings.sendto_max_recipients}.")
        return

    await state.update_data(sendto_recipients=usernames)
    await state.set_state(AdminStates.waiting_for_sendto_content)
    await message.answer(
        f"Ок, получатели: {len(usernames)}. Отправьте следующим сообщением текст/медиа для доставки.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="sendto_cancel")]
        ])
    )


@router.message(AdminStates.waiting_for_sendto_content)
@role_required(AdminRole.MANAGER)
async def sendto_content_received(message: Message, state: FSMContext):
    """Handle content for /sendto command and dispatch sending."""
    if _is_cancel_text(message.text):
        await state.clear()
        await message.answer("❌ Отправка отменена.")
        return

    if message.text and message.text.startswith("/"):
        await message.answer("Пожалуйста, отправьте контент для рассылки, а не команду. Или отмените отправку.")
        return

    try:
        content_items = _extract_broadcast_items(message)
    except ValueError:
        await message.answer("❌ Этот тип сообщения не поддерживается для отправки.")
        return

    data = await state.get_data()
    usernames = data.get("sendto_recipients", [])
    await state.clear()

    if not usernames:
        await message.answer("❌ Не найдены получатели. Начните заново с команды /sendto.")
        return

    async for session in get_db():
        service = SendToService(session, message.bot)
        found_users, not_found_usernames = await service.find_recipients(usernames)

        summary_lines = []
        if not found_users:
            await message.answer("Не удалось найти ни одного из указанных пользователей.")
            return

        await message.answer(f"Начинаю отправку {len(found_users)} пользователям...")

        send_results = await service.send_messages(
            admin_user_id=message.from_user.id,
            recipients=found_users,
            content_items=content_items,
            throttle_rate=settings.sendto_throttle_rate,
        )
        
        sent_count = send_results.get(AdminRole.SENT, 0)
        failed_count = send_results.get(AdminRole.FAILED, 0) + send_results.get(AdminRole.BLOCKED, 0)
        
        summary_lines.append(f"✅ Отправлено: {sent_count}")
        if failed_count > 0:
            summary_lines.append(f"❌ Не доставлено: {failed_count}")
        if not_found_usernames:
            summary_lines.append(f"🤷‍♂️ Не найдены: {len(not_found_usernames)} ({', '.join(not_found_usernames)})")

        await message.answer("\n".join(summary_lines))
        break


@router.callback_query(F.data == "sendto_cancel", StateFilter("*"))
async def sendto_cancel(callback: CallbackQuery, state: FSMContext):
    """Cancel sendto operation."""
    await state.clear()
    await callback.message.edit_text("❌ Отправка отменена.")
    await callback.answer()
