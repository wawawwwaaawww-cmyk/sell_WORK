"""In-memory service for managing manual dialog sessions between managers and users."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional


@dataclass
class ManualDialogSession:
    """Represents an active manual dialog session."""

    user_id: int
    user_telegram_id: int
    manager_telegram_id: int
    manager_username: Optional[str] = None
    manager_full_name: Optional[str] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def manager_display(self) -> str:
        """Return human-friendly identifier for the manager."""
        if self.manager_username:
            return f"@{self.manager_username}"
        if self.manager_full_name:
            return self.manager_full_name
        return f"ID {self.manager_telegram_id}"


class ManualDialogError(Exception):
    """Base exception for manual dialog errors."""


class DialogAlreadyInProgressError(ManualDialogError):
    """Raised when attempting to start a dialog that is already handled by another manager."""


class ManagerBusyError(ManualDialogError):
    """Raised when a manager is already handling another dialog."""


class ManualDialogService:
    """Service responsible for tracking manual dialog sessions."""

    def __init__(self) -> None:
        self._user_sessions: Dict[int, ManualDialogSession] = {}
        self._manager_sessions: Dict[int, ManualDialogSession] = {}
        self._lock = asyncio.Lock()

    async def activate_dialog(
        self,
        *,
        user_id: int,
        user_telegram_id: int,
        manager_telegram_id: int,
        manager_username: Optional[str],
        manager_full_name: Optional[str],
    ) -> ManualDialogSession:
        """Activate manual dialog for the given user and manager."""
        async with self._lock:
            existing_user_session = self._user_sessions.get(user_id)
            if existing_user_session:
                if existing_user_session.manager_telegram_id == manager_telegram_id:
                    return existing_user_session
                raise DialogAlreadyInProgressError(existing_user_session.manager_display)

            existing_manager_session = self._manager_sessions.get(manager_telegram_id)
            if existing_manager_session and existing_manager_session.user_id != user_id:
                raise ManagerBusyError(existing_manager_session.manager_display)

            session = ManualDialogSession(
                user_id=user_id,
                user_telegram_id=user_telegram_id,
                manager_telegram_id=manager_telegram_id,
                manager_username=manager_username,
                manager_full_name=manager_full_name,
            )
            self._user_sessions[user_id] = session
            self._manager_sessions[manager_telegram_id] = session
            return session

    async def deactivate_dialog_by_user(self, user_id: int) -> Optional[ManualDialogSession]:
        """Deactivate manual dialog for the specified user."""
        async with self._lock:
            session = self._user_sessions.pop(user_id, None)
            if session:
                self._manager_sessions.pop(session.manager_telegram_id, None)
            return session

    async def deactivate_dialog_by_manager(self, manager_telegram_id: int) -> Optional[ManualDialogSession]:
        """Deactivate manual dialog handled by the specified manager."""
        async with self._lock:
            session = self._manager_sessions.pop(manager_telegram_id, None)
            if session:
                self._user_sessions.pop(session.user_id, None)
            return session

    def get_session_by_user(self, user_id: int) -> Optional[ManualDialogSession]:
        """Return manual dialog session for the user if active."""
        return self._user_sessions.get(user_id)

    def get_session_by_manager(self, manager_telegram_id: int) -> Optional[ManualDialogSession]:
        """Return manual dialog session handled by the manager if active."""
        return self._manager_sessions.get(manager_telegram_id)

    def is_user_in_manual_mode(self, user_id: int) -> bool:
        """Check whether the user dialog is in manual mode."""
        return user_id in self._user_sessions

    def is_manager_busy(self, manager_telegram_id: int) -> bool:
        """Check whether the manager already handles a dialog."""
        return manager_telegram_id in self._manager_sessions


manual_dialog_service = ManualDialogService()

__all__ = [
    "manual_dialog_service",
    "ManualDialogSession",
    "ManualDialogError",
    "DialogAlreadyInProgressError",
    "ManagerBusyError",
]
