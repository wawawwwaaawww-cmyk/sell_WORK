"""Helpers for managing bonus file metadata and storage."""

import json
import logging
import os
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


class BonusContentManager:
    """Centralized access to bonus file storage and metadata."""

    DEFAULT_FILENAME = "bonus.pdf"
    DEFAULT_CAPTION = "Бонус текст"
    BONUS_DIR = Path(os.getenv("BONUS_DIR", Path(__file__).resolve().parents[2] / "bonus"))
    METADATA_FILE = BONUS_DIR / "metadata.json"

    @classmethod
    def ensure_storage(cls) -> Path:
        """Ensure the bonus storage directory exists."""
        storage_path = cls.BONUS_DIR
        storage_path.mkdir(parents=True, exist_ok=True)
        logger.info("Bonus storage directory ensured at %s", storage_path)
        return storage_path

    @classmethod
    def load_metadata(cls) -> Tuple[str, str]:
        """Load current bonus metadata or return defaults."""
        cls.ensure_storage()
        try:
            if cls.METADATA_FILE.exists():
                with cls.METADATA_FILE.open("r", encoding="utf-8") as file_obj:
                    payload = json.load(file_obj)
                filename = payload.get("filename") or cls.DEFAULT_FILENAME
                caption = payload.get("caption") or cls.DEFAULT_CAPTION
                logger.info("Loaded bonus metadata filename=%s", filename)
            else:
                filename = cls.DEFAULT_FILENAME
                caption = cls.DEFAULT_CAPTION
                logger.info(
                    "Bonus metadata file missing, using defaults filename=%s", filename
                )
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("Failed to read bonus metadata: %s", exc)
            filename = cls.DEFAULT_FILENAME
            caption = cls.DEFAULT_CAPTION
        return filename, caption

    @classmethod
    def load_published_bonus(cls) -> Tuple[Path, str]:
        """Resolve the published bonus file path with caption."""
        filename, caption = cls.load_metadata()
        storage_path = cls.ensure_storage()
        file_path = storage_path / filename
        if not file_path.exists():
            logger.warning("Published bonus file %s not found, attempting fallback", filename)
            fallback = storage_path / cls.DEFAULT_FILENAME
            if fallback.exists():
                file_path = fallback
                logger.info("Fallback bonus file will be used from %s", file_path)
        logger.info("Resolved published bonus file at %s", file_path)
        return file_path, caption

    @classmethod
    def persist_metadata(cls, filename: str, caption: str) -> None:
        """Persist new bonus metadata to disk."""
        cls.ensure_storage()
        payload = {"filename": filename, "caption": caption}
        with cls.METADATA_FILE.open("w", encoding="utf-8") as file_obj:
            json.dump(payload, file_obj, ensure_ascii=False, indent=2)
        logger.info("Updated bonus metadata filename=%s", filename)

    @classmethod
    def target_path(cls, filename: str) -> Path:
        """Return target path inside bonus storage for provided filename."""
        storage_path = cls.ensure_storage()
        target = storage_path / filename
        logger.info("Calculated target path for bonus file %s -> %s", filename, target)
        return target
