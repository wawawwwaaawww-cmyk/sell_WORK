"""Service for handling /sendto command logic."""

import asyncio
from collections import Counter
from typing import List, Dict, Any, Tuple

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import User, AdminOutboundMessage, AdminOutboundResult, AdminOutboundStatus
from ..repositories.user_repository import UserRepository
from .manual_dialog_service import manual_dialog_service

logger = structlog.get_logger(__name__)


class SendToService:
    """Handles logic for sending messages from admins to specific users."""

    def __init__(self, session: AsyncSession, bot: Bot):
        self.session = session
        self.bot = bot
        self.user_repo = UserRepository(session)

    async def find_recipients(self, usernames: List[str]) -> Tuple[List[User], List[str]]:
        """Find users by usernames, return found users and not_found usernames."""
        found_users = await self.user_repo.get_by_usernames(usernames)
        found_usernames = {user.username.lower() for user in found_users if user.username}
        not_found = [uname for uname in usernames if uname.lower() not in found_usernames]
        return found_users, not_found

    async def send_messages(
        self,
        admin_user_id: int,
        recipients: List[User],
        content_items: List[Dict[str, Any]],
        throttle_rate: float,
    ) -> Dict[str, Any]:
        """Send content to a list of recipients and log results."""
        
        text_snippet = next(
            (item.get("plain_text", "")[:500] for item in content_items if item.get("type") == "text"),
            None
        )
        if not text_snippet:
            text_snippet = next(
                (item.get("plain_caption", "")[:500] for item in content_items if item.get("plain_caption")),
                None
            )

        content_kind = ", ".join(sorted({item["type"] for item in content_items}))
        media_ids = [item["file_id"] for item in content_items if "file_id" in item]

        outbound_message = AdminOutboundMessage(
            admin_user_id=admin_user_id,
            recipients=[user.username for user in recipients if user.username],
            content_kind=content_kind,
            text_snippet=text_snippet,
            media_ids=media_ids,
        )
        self.session.add(outbound_message)
        await self.session.flush()

        results: List[AdminOutboundResult] = []
        status_counts = Counter()

        for user in recipients:
            dialog_session = manual_dialog_service.get_session_by_user(user.id)
            is_manual_dialog = dialog_session is not None
            target_chat_id = dialog_session.manager_telegram_id if is_manual_dialog else user.telegram_id

            try:
                for item in content_items:
                    await self._send_item(target_chat_id, item)
                
                results.append(AdminOutboundResult(
                    outbound_id=outbound_message.id,
                    recipient_user_id=user.id,
                    status=AdminOutboundStatus.SENT,
                ))
                status_counts[AdminOutboundStatus.SENT] += 1
                
                if is_manual_dialog and dialog_session.manager_telegram_id != admin_user_id:
                    logger.warning("sendto.manual_dialog.foreign_manager", user_id=user.id, admin_id=admin_user_id)

            except TelegramForbiddenError:
                results.append(AdminOutboundResult(
                    outbound_id=outbound_message.id,
                    recipient_user_id=user.id,
                    status=AdminOutboundStatus.BLOCKED,
                    error_code="forbidden",
                ))
                status_counts[AdminOutboundStatus.BLOCKED] += 1
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                # Simplified retry logic for now
                try:
                    for item in content_items:
                        await self._send_item(target_chat_id, item)
                    results.append(AdminOutboundResult(
                        outbound_id=outbound_message.id,
                        recipient_user_id=user.id,
                        status=AdminOutboundStatus.SENT,
                    ))
                    status_counts[AdminOutboundStatus.SENT] += 1
                except Exception as retry_exc:
                    results.append(AdminOutboundResult(
                        outbound_id=outbound_message.id,
                        recipient_user_id=user.id,
                        status=AdminOutboundStatus.FAILED,
                        error_code=str(retry_exc),
                    ))
                    status_counts[AdminOutboundStatus.FAILED] += 1
            except Exception as e:
                results.append(AdminOutboundResult(
                    outbound_id=outbound_message.id,
                    recipient_user_id=user.id,
                    status=AdminOutboundStatus.FAILED,
                    error_code=str(e),
                ))
                status_counts[AdminOutboundStatus.FAILED] += 1
            
            await asyncio.sleep(throttle_rate)

        self.session.add_all(results)
        await self.session.commit()

        return dict(status_counts)

    async def _send_item(self, chat_id: int, item: Dict[str, Any]):
        """Send a single content item."""
        item_type = item.get("type")
        kwargs = {"chat_id": chat_id}

        if item_type == "text":
            await self.bot.send_message(text=item["text"], parse_mode=item.get("parse_mode"), **kwargs)
        elif item_type == "photo":
            await self.bot.send_photo(photo=item["file_id"], caption=item.get("caption"), parse_mode=item.get("parse_mode"), **kwargs)
        elif item_type == "video":
            await self.bot.send_video(video=item["file_id"], caption=item.get("caption"), parse_mode=item.get("parse_mode"), **kwargs)
        elif item_type == "document":
            await self.bot.send_document(document=item["file_id"], caption=item.get("caption"), parse_mode=item.get("parse_mode"), **kwargs)
        elif item_type == "audio":
            await self.bot.send_audio(audio=item["file_id"], caption=item.get("caption"), parse_mode=item.get("parse_mode"), **kwargs)
        elif item_type == "voice":
            await self.bot.send_voice(voice=item["file_id"], **kwargs)
        else:
            raise ValueError(f"Unsupported content type: {item_type}")