"""Admin handlers for managing sell scripts."""

import structlog
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.script_service import ScriptService
from app.services.script_exceptions import ScriptError

router = Router()
log = structlog.get_logger(__name__)


@router.message(Command("reindex_scripts"))
async def reindex_scripts(message: Message, session: AsyncSession):
    """Handle manual re-indexing of sell scripts."""
    if message.from_user.id not in settings.admin_ids_list:
        await message.answer("Access denied.")
        return

    await message.answer("Starting script re-indexing... ⏳")
    
    try:
        script_service = ScriptService(session)
        stats = await script_service.index_scripts_from_file(settings.scripts_index_path)
        await message.answer(
            f"Re-indexing complete! ✅\n"
            f"Processed: {stats['processed']}\n"
            f"Added/Updated: {stats['processed']}"
        )
        log.info("Manual re-indexing completed.", admin_id=message.from_user.id, stats=stats)
    except ScriptError as e:
        log.error("Script re-indexing failed.", error=str(e), admin_id=message.from_user.id)
        await message.answer(f"Error during re-indexing: {e}")
    except Exception:
        log.exception("Unhandled exception during script re-indexing.", admin_id=message.from_user.id)
        await message.answer("An unexpected error occurred. Check logs for details.")