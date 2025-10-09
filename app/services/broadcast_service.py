"""Broadcast service for sending messages to users."""

from typing import List, Optional, Dict, Any, Tuple
import asyncio

import structlog
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Broadcast, UserSegment
from app.services.ab_testing_service import ABTestingService


class BroadcastRepository:
    """Repository for broadcast database operations."""
    
    def __init__(self, session: AsyncSession):
        self.session = session
        self.logger = structlog.get_logger()
    
    async def create_broadcast(
        self,
        title: str,
        body: str,
        buttons: Optional[Dict[str, Any]] = None,
        segment_filter: Optional[Dict[str, Any]] = None
    ) -> Broadcast:
        """Create a new broadcast."""
        broadcast = Broadcast(
            title=title,
            body=body,
            buttons=buttons,
            segment_filter=segment_filter
        )
        
        self.session.add(broadcast)
        await self.session.flush()
        await self.session.refresh(broadcast)
        
        self.logger.info(
            "Broadcast created",
            broadcast_id=broadcast.id,
            title=title
        )
        
        return broadcast
    
    async def get_broadcast_by_id(self, broadcast_id: int) -> Optional[Broadcast]:
        """Get broadcast by ID."""
        stmt = select(Broadcast).where(Broadcast.id == broadcast_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class BroadcastService:
    """Service for broadcast campaigns."""
    
    def __init__(self, bot: Bot, session: AsyncSession):
        self.bot = bot
        self.session = session
        self.repository = BroadcastRepository(session)
        self.ab_testing_service = ABTestingService(session)
        self.logger = structlog.get_logger()
    
    async def create_simple_broadcast(
        self,
        title: str,
        body: str,
        segment_filter: Optional[Dict[str, Any]] = None,
        buttons: Optional[List[Dict[str, str]]] = None
    ) -> Broadcast:
        """Create a simple broadcast (no A/B testing)."""
        
        # Convert buttons to format for storage
        buttons_data = None
        if buttons:
            buttons_data = {"buttons": buttons}
        
        broadcast = await self.repository.create_broadcast(
            title=title,
            body=body,
            buttons=buttons_data,
            segment_filter=segment_filter
        )
        
        return broadcast
    
    async def create_ab_broadcast(
        self,
        test_name: str,
        variant_a_title: str,
        variant_a_body: str,
        variant_b_title: str,
        variant_b_body: str,
        segment_filter: Optional[Dict[str, Any]] = None,
        variant_a_buttons: Optional[List[Dict[str, str]]] = None,
        variant_b_buttons: Optional[List[Dict[str, str]]] = None,
        population: int = 20
    ) -> Tuple[bool, str, Optional[int]]:
        """Create A/B test broadcast."""
        try:
            # Convert buttons to format for A/B testing
            a_buttons = {"buttons": variant_a_buttons} if variant_a_buttons else None
            b_buttons = {"buttons": variant_b_buttons} if variant_b_buttons else None
            
            # Create A/B test
            ab_test = await self.ab_testing_service.create_ab_test(
                name=test_name,
                variant_a_title=variant_a_title,
                variant_a_body=variant_a_body,
                variant_b_title=variant_b_title,
                variant_b_body=variant_b_body,
                population=population,
                variant_a_buttons=a_buttons,
                variant_b_buttons=b_buttons
            )
            
            return True, "A/B test broadcast created", ab_test.id
            
        except Exception as e:
            self.logger.error("Error creating A/B broadcast", error=str(e))
            return False, "Error creating A/B test", None
    
    async def send_simple_broadcast(
        self,
        broadcast_id: int,
        delay_between_messages: float = 0.1
    ) -> Dict[str, int]:
        """Send simple broadcast to all target users."""
        try:
            broadcast = await self.repository.get_broadcast_by_id(broadcast_id)
            if not broadcast:
                return {"error": 1, "sent": 0, "failed": 0}
            
            # Get target users
            users = await self._get_target_users(broadcast.segment_filter)
            
            # Prepare message
            keyboard = self._build_keyboard(broadcast.buttons)
            
            sent_count = 0
            failed_count = 0
            
            # Send messages
            for user in users:
                try:
                    await self.bot.send_message(
                        chat_id=user.telegram_id,
                        text=broadcast.body,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                    sent_count += 1
                    
                    # Delay to avoid rate limits
                    if delay_between_messages > 0:
                        await asyncio.sleep(delay_between_messages)
                        
                except Exception as e:
                    self.logger.warning(
                        "Failed to send broadcast message",
                        user_id=user.id,
                        error=str(e)
                    )
                    failed_count += 1
            
            self.logger.info(
                "Simple broadcast completed",
                broadcast_id=broadcast_id,
                sent=sent_count,
                failed=failed_count
            )
            
            return {"sent": sent_count, "failed": failed_count, "total": len(users)}
            
        except Exception as e:
            self.logger.error("Error sending simple broadcast", error=str(e))
            return {"error": 1, "sent": 0, "failed": 0}
    
    async def send_ab_test_broadcast(
        self,
        ab_test_id: int,
        delay_between_messages: float = 0.1
    ) -> Dict[str, Any]:
        """Send A/B test broadcast to test population."""
        try:
            # Start the A/B test
            success, message = await self.ab_testing_service.start_ab_test(ab_test_id)
            if not success:
                return {"error": message}
            
            # Get test and variants
            ab_test = await self.ab_testing_service.repository.get_ab_test_by_id(ab_test_id)
            variants = await self.ab_testing_service.repository.get_test_variants(ab_test_id)
            
            if not ab_test or len(variants) < 2:
                return {"error": "Invalid A/B test setup"}
            
            # Get all users (no segment filter for now - can be added later)
            all_users = await self._get_target_users(None)
            
            # Select test population
            test_users = await self.ab_testing_service.select_test_users(
                all_users, ab_test.population
            )
            
            sent_stats = {"A": 0, "B": 0}
            failed_stats = {"A": 0, "B": 0}
            
            # Send messages to test users
            for user in test_users:
                try:
                    # Assign variant to user
                    variant = await self.ab_testing_service.assign_variant(user, variants)
                    
                    # Build keyboard for variant
                    keyboard = self._build_keyboard(variant.buttons)
                    
                    # Send message
                    await self.bot.send_message(
                        chat_id=user.telegram_id,
                        text=variant.body,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                    
                    # Record delivery
                    await self.ab_testing_service.record_delivery(
                        ab_test_id, variant.variant_code, user.id
                    )
                    
                    sent_stats[variant.variant_code] += 1
                    
                    # Delay to avoid rate limits
                    if delay_between_messages > 0:
                        await asyncio.sleep(delay_between_messages)
                        
                except Exception as e:
                    variant_code = "A"  # Default for error tracking
                    try:
                        variant = await self.ab_testing_service.assign_variant(user, variants)
                        variant_code = variant.variant_code
                    except:
                        pass
                    
                    self.logger.warning(
                        "Failed to send A/B test message",
                        user_id=user.id,
                        variant=variant_code,
                        error=str(e)
                    )
                    failed_stats[variant_code] += 1
            
            total_sent = sum(sent_stats.values())
            total_failed = sum(failed_stats.values())
            
            self.logger.info(
                "A/B test broadcast completed",
                test_id=ab_test_id,
                sent_stats=sent_stats,
                failed_stats=failed_stats,
                total_population=len(test_users)
            )
            
            return {
                "sent": total_sent,
                "failed": total_failed,
                "total_population": len(test_users),
                "variant_stats": {
                    "A": {"sent": sent_stats["A"], "failed": failed_stats["A"]},
                    "B": {"sent": sent_stats["B"], "failed": failed_stats["B"]}
                }
            }
            
        except Exception as e:
            self.logger.error("Error sending A/B test broadcast", error=str(e))
            return {"error": str(e)}
    
    async def send_winner_broadcast(
        self,
        ab_test_id: int,
        delay_between_messages: float = 0.1
    ) -> Dict[str, Any]:
        """Send winning variant to remaining users."""
        try:
            # Get winner variant
            winner_variant = await self.ab_testing_service.get_winner_variant(ab_test_id)
            if not winner_variant:
                return {"error": "No winner variant found"}
            
            # Get all users
            all_users = await self._get_target_users(None)
            
            # Get users who already received test (this would need tracking in real implementation)
            # For now, send to all users
            remaining_users = all_users
            
            # Build keyboard
            keyboard = self._build_keyboard(winner_variant.buttons)
            
            sent_count = 0
            failed_count = 0
            
            # Send winner variant to remaining users
            for user in remaining_users:
                try:
                    await self.bot.send_message(
                        chat_id=user.telegram_id,
                        text=winner_variant.body,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                    sent_count += 1
                    
                    # Delay to avoid rate limits
                    if delay_between_messages > 0:
                        await asyncio.sleep(delay_between_messages)
                        
                except Exception as e:
                    self.logger.warning(
                        "Failed to send winner broadcast message",
                        user_id=user.id,
                        error=str(e)
                    )
                    failed_count += 1
            
            self.logger.info(
                "Winner broadcast completed",
                test_id=ab_test_id,
                winner_variant=winner_variant.variant_code,
                sent=sent_count,
                failed=failed_count
            )
            
            return {
                "sent": sent_count,
                "failed": failed_count,
                "total": len(remaining_users),
                "winner_variant": winner_variant.variant_code
            }
            
        except Exception as e:
            self.logger.error("Error sending winner broadcast", error=str(e))
            return {"error": str(e)}
    
    async def _get_target_users(
        self,
        segment_filter: Optional[Dict[str, Any]]
    ) -> List[User]:
        """Get target users based on segment filter."""
        stmt = select(User).where(User.is_blocked == False)
        
        if segment_filter:
            # Apply segment filters
            if "segments" in segment_filter:
                segments = segment_filter["segments"]
                stmt = stmt.where(User.segment.in_(segments))
            
            if "min_score" in segment_filter:
                stmt = stmt.where(User.lead_score >= segment_filter["min_score"])
            
            if "funnel_stages" in segment_filter:
                stages = segment_filter["funnel_stages"]
                stmt = stmt.where(User.funnel_stage.in_(stages))
        
        result = await self.session.execute(stmt)
        return result.scalars().all()
    
    def _build_keyboard(
        self,
        buttons_data: Optional[Dict[str, Any]]
    ) -> Optional[InlineKeyboardMarkup]:
        """Build inline keyboard from buttons data."""
        if not buttons_data or "buttons" not in buttons_data:
            return None
        
        buttons = buttons_data["buttons"]
        if not buttons:
            return None
        
        keyboard = InlineKeyboardBuilder()
        
        for button in buttons:
            keyboard.add(InlineKeyboardButton(
                text=button["text"],
                callback_data=button["callback_data"]
            ))
        
        keyboard.adjust(1)  # One button per row
        return keyboard.as_markup()
    
    async def get_broadcast_preview(
        self,
        title: str,
        body: str,
        buttons: Optional[List[Dict[str, str]]] = None
    ) -> str:
        """Generate broadcast preview."""
        preview = f"📢 **{title}**\n\n{body}"
        
        if buttons:
            preview += "\n\n🔘 **Кнопки:**\n"
            for i, button in enumerate(buttons, 1):
                preview += f"{i}. {button['text']}\n"
        
        preview += f"\n📊 Будет отправлено пользователям согласно фильтрам"
        
        return preview