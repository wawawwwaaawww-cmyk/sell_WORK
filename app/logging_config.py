"""Centralized logging configuration for the Telegram Sales Bot."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import structlog
from structlog.types import Processor

from app.config import settings

# Define the project root to calculate the log file path
project_root = Path(__file__).parent.parent
log_file_path = project_root / "logs/bot_interactions.log"

INTERACTION_LOGGER_PREFIX = "bot.interactions"

_logging_configured = False


def _russian_message_renderer(
    _: logging.Logger,
    __: str,
    event_dict: dict[str, Any],
) -> str:
    """Format structlog event into a readable Russian message."""

    timestamp = event_dict.pop("timestamp", "")
    level = str(event_dict.pop("level", "")).upper()
    event = str(event_dict.pop("event", ""))
    logger_name = event_dict.pop("logger", "")

    field_labels = {
        "request_id": "запрос",
        "user_id": "пользователь",
        "username": "ник",
        "full_name": "имя",
        "event_type": "тип события",
        "content_type": "тип контента",
        "text": "текст",
        "data": "данные",
        "status": "статус",
        "handler": "обработчик",
    }

    details: list[str] = []

    for key, label in field_labels.items():
        value = event_dict.pop(key, None)

        if value in (None, "", []):
            continue

        if isinstance(value, dict):
            value = ", ".join(f"{dict_key}={dict_value}" for dict_key, dict_value in value.items())

        details.append(f"{label}: {value}")

    for key, value in event_dict.items():
        if value in (None, "", []):
            continue
        details.append(f"{key}={value}")

    parts = [str(timestamp) if timestamp else ""]

    if level:
        parts.append(level)

    if logger_name:
        parts.append(f"источник: {logger_name}")

    parts.append(event)

    if details:
        parts.append("; ".join(details))

    return " | ".join(part for part in parts if part)


def setup_logging(*, enable_tracing: bool = False) -> None:
    """
    Configure structured logging for the entire application.

    The configuration writes verbose diagnostics to stdout while only user-facing
    interaction logs are persisted in ``logs/bot_interactions.log`` in a
    human-readable Russian format.
    """

    global _logging_configured

    if _logging_configured:
        return

    log_level = settings.log_level.upper()

    # Ensure log directory exists before configuring handlers
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    # Define shared processors for structlog
    shared_processors: list[Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="%d.%m.%Y %H:%M:%S"),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # Configure standard logging to be hijacked by structlog
    logging.basicConfig(
        level=log_level,
        stream=sys.stdout,
        format="%(message)s",
    )

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Define formatter for console output (human-readable)
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=True),
        foreign_pre_chain=shared_processors,
    )

    # Define formatter for file output (Russian readable format)
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processor=_russian_message_renderer,
        foreign_pre_chain=shared_processors,
    )

    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)

    # Create file handler writing to a single consolidated file
    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setFormatter(file_formatter)
    file_handler.addFilter(logging.Filter(INTERACTION_LOGGER_PREFIX))

    # Get the root logger and remove existing handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add our configured handlers
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(log_level)

    # Reduce noise from third-party libraries in file logs
    for noisy_logger in ("aiogram", "apscheduler", "sqlalchemy", "asyncio", "urllib3"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    logger = structlog.get_logger("logging_setup")
    logger.info(
        "Логирование настроено",
        level=log_level,
        file_path=str(log_file_path),
    )

    _logging_configured = True
