"""Repository for storing and retrieving system-wide settings."""

from __future__ import annotations

from typing import Any, Optional

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SystemSetting


class SystemSettingsRepository:
    """Simple key/value repository backed by the system_settings table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.logger = structlog.get_logger(__name__)

    async def get(self, key: str) -> Optional[SystemSetting]:
        """Fetch a setting row by key."""
        stmt = select(SystemSetting).where(SystemSetting.key == key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_value(self, key: str, default: Any = None) -> Any:
        """Convenience helper returning the stored 'value' field."""
        setting = await self.get(key)
        if setting is None or setting.value is None:
            return default
        # Values are stored as {"value": <actual>}
        if isinstance(setting.value, dict) and "value" in setting.value:
            return setting.value["value"]
        return setting.value

    async def set_value(
        self,
        key: str,
        value: Any,
        *,
        description: Optional[str] = None,
    ) -> SystemSetting:
        """Insert or update a setting row."""
        setting = await self.get(key)
        payload = {"value": value}
        if setting is None:
            setting = SystemSetting(key=key, value=payload, description=description)
            self.session.add(setting)
            await self.session.flush()
            self.logger.info("system_setting_created", key=key, value=value)
        else:
            setting.value = payload
            if description is not None:
                setting.description = description
            await self.session.flush()
            self.logger.info("system_setting_updated", key=key, value=value)
        return setting

