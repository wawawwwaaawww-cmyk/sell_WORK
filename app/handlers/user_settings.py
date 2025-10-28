"""Handlers for user self-service actions (e.g., data cleanup)."""

import structlog
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.models import User
from app.services.logging_service import ConversationLoggingService
from app.services.user_service import UserService
from app.utils.callbacks import Callbacks


router = Router()
logger = structlog.get_logger()


@router.message(Command("reset"))
async def handle_reset_command(message: Message, user: User, **kwargs) -> None:
    """Ask the user to confirm wiping their stored data."""
    session = kwargs.get("session")
    conversation_logger = ConversationLoggingService(session) if session else None

    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="Delete my data",
        callback_data=Callbacks.USER_RESET_CONFIRM,
    ))
    builder.add(InlineKeyboardButton(
        text="Keep everything",
        callback_data=Callbacks.USER_RESET_CANCEL,
    ))
    builder.adjust(1)

    prompt = (
        "Delete all saved data?\n\n"
        "I will remove conversation history, leads, and other linked records. "
        "You can start fresh with /start afterwards."
    )

    if conversation_logger:
        await conversation_logger.log_user_message(
            user_id=user.id,
            text=message.text or "/reset",
            bot=message.bot,
            user=user,
            telegram_user=message.from_user,
            metadata={"source": "command"},
            source_message=message,
        )
        await conversation_logger.send_or_edit(
            message,
            text=prompt,
            user_id=user.id,
            user=user,
            reply_markup=builder.as_markup(),
            prefer_edit=False,
        )
    else:
        await message.answer(prompt, reply_markup=builder.as_markup())


@router.callback_query(F.data == Callbacks.USER_RESET_CANCEL)
async def handle_reset_cancel(callback: CallbackQuery, **kwargs) -> None:
    """Stop the cleanup flow."""
    await callback.answer("No worries, nothing was deleted")
    note = "Okay, we keep your data. Run /reset anytime if you change your mind."
    try:
        await callback.message.edit_text(note)
    except Exception:
        await callback.message.answer(note)


@router.callback_query(F.data == Callbacks.USER_RESET_CONFIRM)
async def handle_reset_confirm(
    callback: CallbackQuery,
    user: User,
    user_service: UserService,
    **kwargs,
) -> None:
    """Purge all user-related data from the database."""
    user_id = getattr(user, "id", None)
    telegram_id = getattr(user, "telegram_id", None)

    try:
        stats = await user_service.purge_user_data(user)
        logger.info(
            "User initiated self-cleanup",
            user_id=user_id,
            telegram_id=telegram_id,
            stats=stats,
        )
        await callback.answer("Done, data wiped.", show_alert=True)
        confirmation = (
            "All your records are gone.\n"
            "Use /start to begin again whenever you are ready."
        )
        try:
            await callback.message.edit_text(confirmation)
        except Exception:
            await callback.message.answer(confirmation)
    except Exception as error:
        logger.error(
            "Failed to purge user data",
            user_id=user_id,
            telegram_id=telegram_id,
            error=str(error),
            exc_info=True,
        )
        await callback.answer("Could not delete the data. Please try again later.", show_alert=True)


def register_handlers(dp) -> None:
    """Register user settings handlers."""
    dp.include_router(router)
