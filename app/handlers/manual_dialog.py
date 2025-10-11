"""Handlers for managing manual dialog control buttons."""

from typing import Optional

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from app.models import User as AppUser
from app.services.manual_dialog_service import (
    DialogAlreadyInProgressError,
    ManagerBusyError,
    manual_dialog_service,
)
from app.services.user_service import UserService

router = Router()
logger = structlog.get_logger(__name__)

START_PREFIX = "manual_dialog:start:"
STOP_PREFIX = "manual_dialog:stop:"


def _build_keyboard(active: bool, user_id: int) -> InlineKeyboardMarkup:
    """Return inline keyboard for manual dialog control."""
    if active:
        button = InlineKeyboardButton(
            text="Завершить диалог",
            callback_data=f"{STOP_PREFIX}{user_id}",
        )
    else:
        button = InlineKeyboardButton(
            text="Продолжить диалог",
            callback_data=f"{START_PREFIX}{user_id}",
        )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def _get_manager_full_name(callback: CallbackQuery) -> Optional[str]:
    """Compose manager full name from callback initiator."""
    user = callback.from_user
    if not user:
        return None
    full_name = " ".join(filter(None, [user.first_name, user.last_name]))
    return full_name if full_name.strip() else None


@router.callback_query(F.data.startswith(START_PREFIX))
async def handle_manual_dialog_start(
    callback: CallbackQuery,
    user_service: UserService,
) -> None:
    """Assign manager to a user dialog when start button pressed."""
    target_user_id = _parse_identifier(callback.data, START_PREFIX)
    if not target_user_id:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return

    target_user: Optional[AppUser] = await user_service.repository.get_by_id(target_user_id)
    if not target_user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    manager = callback.from_user
    if not manager:
        await callback.answer("Не удалось определить менеджера.", show_alert=True)
        return

    try:
        session = await manual_dialog_service.activate_dialog(
            user_id=target_user.id,
            user_telegram_id=target_user.telegram_id,
            manager_telegram_id=manager.id,
            manager_username=manager.username,
            manager_full_name=_get_manager_full_name(callback),
        )
    except DialogAlreadyInProgressError as exc:
        await callback.answer(
            f"Диалог уже ведёт {exc}.",
            show_alert=True,
        )
        return
    except ManagerBusyError:
        await callback.answer(
            "Вы уже ведёте другой диалог. Сначала завершите его.",
            show_alert=True,
        )
        return

    await callback.answer("Режим ручного диалога включён.", show_alert=False)

    try:
        await callback.message.edit_reply_markup(
            reply_markup=_build_keyboard(active=True, user_id=target_user.id)
        )
    except TelegramBadRequest:
        logger.debug(
            "manual_dialog_markup_update_skipped",
            user_id=target_user.id,
            reason="message_not_modified",
        )

    try:
        await callback.bot.send_message(
            chat_id=manager.id,
            text=(
                f"Диалог с пользователем ID {target_user.telegram_id} активирован.\n"
                "Отправляйте сообщения в этот чат, они будут доставлены пользователю."
            ),
        )
    except TelegramForbiddenError:
        logger.warning("manual_dialog_manager_not_initiated", manager_id=manager.id)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning(
            "manual_dialog_manager_notify_failed",
            error=str(exc),
            manager_id=manager.id,
        )

    logger.info(
        "manual_dialog_started",
        user_id=target_user.id,
        telegram_user_id=target_user.telegram_id,
        manager_id=manager.id,
    )


@router.callback_query(F.data.startswith(STOP_PREFIX))
async def handle_manual_dialog_stop(callback: CallbackQuery) -> None:
    """Release manager from manual dialog session."""
    target_user_id = _parse_identifier(callback.data, STOP_PREFIX)
    if not target_user_id:
        await callback.answer("Не удалось определить пользователя.", show_alert=True)
        return

    manager = callback.from_user
    if not manager:
        await callback.answer("Не удалось определить менеджера.", show_alert=True)
        return

    session = manual_dialog_service.get_session_by_manager(manager.id)
    if not session:
        await callback.answer("У вас нет активных диалогов.", show_alert=True)
        return

    if session.user_id != target_user_id:
        await callback.answer("Этот диалог ведёт другой менеджер.", show_alert=True)
        return

    await manual_dialog_service.deactivate_dialog_by_manager(manager.id)
    await callback.answer("Диалог завершён.", show_alert=False)

    try:
        await callback.message.edit_reply_markup(
            reply_markup=_build_keyboard(active=False, user_id=target_user_id)
        )
    except TelegramBadRequest:
        logger.debug(
            "manual_dialog_markup_update_skipped",
            user_id=target_user_id,
            reason="message_not_modified",
        )

    try:
        await callback.bot.send_message(
            chat_id=manager.id,
            text="Диалог завершён. Бот вернулся к автоматическому режиму.",
        )
    except TelegramForbiddenError:
        logger.warning("manual_dialog_manager_not_initiated", manager_id=manager.id)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning(
            "manual_dialog_manager_notify_failed",
            error=str(exc),
            manager_id=manager.id,
        )

    logger.info(
        "manual_dialog_stopped",
        user_id=target_user_id,
        manager_id=manager.id,
    )


def _parse_identifier(value: Optional[str], prefix: str) -> Optional[int]:
    """Parse integer identifier from callback payload."""
    if not value or not value.startswith(prefix):
        return None
    try:
        return int(value.replace(prefix, "", 1))
    except ValueError:
        return None


__all__ = ["router"]
