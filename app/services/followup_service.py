"""Service for handling follow-up messages to inactive users."""

import logging
from typing import List, Dict, Any, Literal, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputMediaVideo, InputMediaDocument
from aiogram.exceptions import TelegramAPIError

from app.models import User, FollowupTemplate
from app.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)

class FollowupService:
    """Manages templates and sending of follow-up messages."""

    def __init__(self, session: AsyncSession, bot: Bot):
        self.session = session
        self.bot = bot
        self.user_repo = UserRepository(session)

    async def get_template(self, kind: Literal['24h', '72h']) -> Optional[FollowupTemplate]:
        """Get a follow-up template by its kind."""
        stmt = select(FollowupTemplate).where(FollowupTemplate.kind == kind)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_template(
        self, kind: Literal['24h', '72h'], text: str, media: List[Dict[str, Any]]
    ) -> FollowupTemplate:
        """Update or create a follow-up template."""
        template = await self.get_template(kind)
        if not template:
            template = FollowupTemplate(kind=kind, title=f"Follow-up {kind}")
            self.session.add(template)
        
        template.text = text
        template.media = media
        await self.session.flush()
        await self.session.refresh(template)
        return template

    def _render_text(self, text: str, user: User) -> str:
        """Render placeholders in the text."""
        if not text:
            return ""
        return text.format(
            first_name=user.first_name or "",
            last_name=user.last_name or "",
            username=user.username or "",
            segment=user.segment or "",
            score=user.lead_score or 0,
        )

    async def send_followup(self, user: User, kind: Literal['24h', '72h']) -> bool:
        """Send a follow-up message to a user."""
        template = await self.get_template(kind)
        if not template:
            logger.warning("Follow-up template not found (kind=%s)", kind)
            return False

        text_to_send = self._render_text(template.text, user)
        media_to_send = template.media or []

        try:
            if not media_to_send:
                if text_to_send:
                    await self.bot.send_message(user.telegram_id, text_to_send)
            elif len(media_to_send) == 1:
                media_item = media_to_send[0]
                media_type = media_item.get("type")
                file_id = media_item.get("file_id")
                caption = self._render_text(media_item.get("caption", ""), user)

                if not caption and text_to_send:
                    caption = text_to_send

                if media_type == "photo":
                    await self.bot.send_photo(user.telegram_id, file_id, caption=caption)
                elif media_type == "video":
                    await self.bot.send_video(user.telegram_id, file_id, caption=caption)
                elif media_type == "document":
                    await self.bot.send_document(user.telegram_id, file_id, caption=caption)
                elif media_type == "audio":
                    await self.bot.send_audio(user.telegram_id, file_id, caption=caption)
                elif media_type == "voice":
                    await self.bot.send_voice(user.telegram_id, file_id)
                
                if not caption and text_to_send:
                     await self.bot.send_message(user.telegram_id, text_to_send)

            else: # Media group
                media_group = []
                first_caption_added = False
                for item in media_to_send:
                    media_type = item.get("type")
                    file_id = item.get("file_id")
                    caption = self._render_text(item.get("caption", ""), user)
                    
                    # Caption can only be on the first item in a media group
                    if not first_caption_added:
                        caption_to_add = caption or text_to_send
                        first_caption_added = True
                    else:
                        caption_to_add = None

                    if media_type == "photo":
                        media_group.append(InputMediaPhoto(media=file_id, caption=caption_to_add))
                    elif media_type == "video":
                        media_group.append(InputMediaVideo(media=file_id, caption=caption_to_add))
                    elif media_type == "document":
                        media_group.append(InputMediaDocument(media=file_id, caption=caption_to_add))
                
                if media_group:
                    await self.bot.send_media_group(user.telegram_id, media_group)
                
                if not first_caption_added and text_to_send:
                    await self.bot.send_message(user.telegram_id, text_to_send)

            logger.info("Follow-up sent (user_id=%s, kind=%s)", user.id, kind)
            return True

        except TelegramAPIError as e:
            logger.error(
                "Failed to send follow-up (user_id=%s, kind=%s, error=%s)",
                user.id,
                kind,
                e,
            )
            if "bot was blocked by the user" in str(e):
                user.is_blocked = True
                await self.session.flush()
            return False
        except Exception as e:
            logger.exception(
                "Unexpected error sending follow-up (user_id=%s, kind=%s)",
                user.id,
                kind,
            )
            return False