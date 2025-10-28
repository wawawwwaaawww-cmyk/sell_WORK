
"""Bonus repository for managing bonus content."""

from __future__ import annotations

from typing import List

import structlog
from aiogram.types import FSInputFile, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Material, MaterialType, MaterialVersion
from app.repositories.material_repository import MaterialRepository
from app.services.bonus_content_manager import BonusContentManager


class BonusRepository:
    """Repository wrapper around MaterialRepository for bonus content."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.material_repository = MaterialRepository(session)
        self.logger = structlog.get_logger()

    async def get_welcome_bonuses(self, limit: int = 5) -> List[Material]:
        """Return materials tagged as welcome bonuses."""
        bonuses = await self.material_repository.get_materials_by_tags(["welcome", "bonus"], limit)
        if len(bonuses) < limit:
            extras = await self.material_repository.get_materials_by_type(MaterialType.BONUS, limit)
            for material in extras:
                if material not in bonuses:
                    bonuses.append(material)
                if len(bonuses) >= limit:
                    break
        return bonuses[:limit]

    async def get_bonus_by_tag(self, tag: str, limit: int = 3) -> List[Material]:
        """Return bonus materials filtered by tag."""
        return await self.material_repository.get_materials_by_tags([tag], limit)


class BonusService:
    """Service orchestrating bonus selection and formatting."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = BonusRepository(session)
        self.logger = structlog.get_logger()

    async def send_bonus(self, message: Message) -> None:
        """Send the bonus file to the user."""
        try:
            bonus_file_path, bonus_caption = BonusContentManager.load_published_bonus()
            document = FSInputFile(bonus_file_path)

            await message.answer_document(document, caption=bonus_caption)
            self.logger.info("Bonus file sent successfully", user_id=message.from_user.id)
        except FileNotFoundError:
            self.logger.error("Bonus file not found.", path=BonusContentManager.get_bonus_path())
            await message.answer("К сожалению, бонусный файл сейчас недоступен. Мы уже работаем над этим!")
        except Exception as e:
            self.logger.error("Failed to send bonus file", error=str(e), exc_info=True)
            raise

    async def get_welcome_bonus_text(self) -> str:
        """Prepare formatted text with welcome bonuses."""
        bonuses = await self.repository.get_welcome_bonuses()
        if not bonuses:
            return self._default_bonus_text()

        lines: List[str] = ["🎁 **Отлично! Держи свои бонусы:**", ""]
        for index, bonus in enumerate(bonuses, start=1):
            lines.append(f"{index}. **{bonus.title}**")
            preview = self._material_preview(bonus)
            if preview:
                lines.append(f"   {preview}")
            link = self._material_link(bonus)
            if link:
                lines.append(f"   🔗 [Получить]({link})")
            lines.append("")
        lines.append("💡 *Эти материалы помогут тебе сделать первые шаги в мире криптовалют безопасно и эффективно!*")
        lines.append("")
        lines.append("Готов подобрать индивидуальную программу обучения? 🎯")
        return "\n".join(lines)

    def _material_preview(self, material: Material, max_length: int = 120) -> str:
        version: MaterialVersion | None = material.active_version
        source = version.extracted_text if version and version.extracted_text else material.summary
        if not source:
            return ""
        preview = source.strip().replace("\n", " ")
        if len(preview) > max_length:
            preview = preview[: max_length - 3] + "..."
        return preview

    def _material_link(self, material: Material) -> str:
        version: MaterialVersion | None = material.active_version
        if version:
            link = version.primary_asset_url
            if link:
                return link
        return ""

    def _default_bonus_text(self) -> str:
        """Fallback text when catalogue has no bonus materials."""
        return """🎁 **Отлично! Держи свои бонусы:**

1. **Гайд "Первые шаги в криптовалютах"**
   Пошаговая инструкция для новичков с примерами
   🔗 [Скачать PDF](https://example.com/guide1)

2. **Чек-лист безопасности**
   Как защитить свои средства от мошенников
   🔗 [Открыть чек-лист](https://example.com/checklist)

3. **Видео "Как выбрать первую биржу"**
   Обзор популярных бирж и их особенностей
   🔗 [Смотреть видео](https://example.com/video1)

4. **Словарь криптотерминов**
   200+ терминов с простыми объяснениями
   🔗 [Открыть словарь](https://example.com/dictionary)

5. **Telegram-канал с аналитикой**
   Ежедневные обзоры рынка от экспертов
   🔗 [Подписаться](https://t.me/cryptoanalysis)

💡 *Эти материалы помогут тебе сделать первые шаги в мире криптовалют безопасно и эффективно!*

Готов подобрать индивидуальную программу обучения? 🎯"""
