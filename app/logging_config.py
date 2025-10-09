"""Centralized logging configuration for the Telegram Sales Bot."""

from __future__ import annotations

import inspect
import logging
import logging.handlers
import sys
import threading
from pathlib import Path
from types import FrameType
from typing import Any

import structlog
from structlog.types import Processor

from app.config import settings

# Define the project root to calculate the log file path
project_root = Path(__file__).parent.parent
log_file_path = project_root / "logs/seller_krypto.log"

_logging_configured = False
_function_tracing_enabled = False
_trace_guard = threading.local()


def _safe_repr(value: Any, max_length: int = 200) -> str:
    """Return a safe, truncated representation of a value for logging."""

    try:
        representation = repr(value)
    except Exception as exc:  # pragma: no cover - defensive programming
        representation = f"<repr-error {exc}>"

    if len(representation) > max_length:
        return f"{representation[:max_length]}...<truncated>"
    return representation


def _should_trace(frame: FrameType) -> bool:
    """Check whether the current frame belongs to the project code base."""

    module_name = frame.f_globals.get("__name__", "")
    if module_name == __name__ and frame.f_code.co_name == "_profile_function":
        return False

    filename = frame.f_code.co_filename
    try:
        file_path = Path(filename).resolve()
    except FileNotFoundError:  # pragma: no cover - handle removed files gracefully
        return False

    try:
        file_path.relative_to(project_root)
    except ValueError:
        return False

    if "venv" in file_path.parts:
        return False

    return True


def _profile_function(frame: FrameType, event: str, arg: Any) -> None:
    """Profile callback used to log every function call inside the project."""

    if not _should_trace(frame):
        return

    if getattr(_trace_guard, "active", False):
        return

    _trace_guard.active = True
    try:
        module_name = frame.f_globals.get("__name__", "unknown_module")
        function_name = frame.f_code.co_name
        logger = structlog.get_logger(module_name)

        if event == "call":
            arg_info = inspect.getargvalues(frame)
            arguments = {
                name: _safe_repr(arg_info.locals.get(name))
                for name in arg_info.args
            }
            if arg_info.varargs:
                arguments[arg_info.varargs] = _safe_repr(
                    arg_info.locals.get(arg_info.varargs, ())
                )
            if arg_info.keywords:
                arguments[arg_info.keywords] = _safe_repr(
                    arg_info.locals.get(arg_info.keywords, {})
                )

            logger.info(
                "function.call",
                function=function_name,
                arguments=arguments,
            )
        elif event == "return":
            logger.info(
                "function.return",
                function=function_name,
                return_value=_safe_repr(arg),
            )
        elif event == "exception":
            exc_type, exc_value, _ = arg
            logger.error(
                "function.exception",
                function=function_name,
                exception_type=getattr(exc_type, "__name__", str(exc_type)),
                exception_message=str(exc_value),
            )
    finally:
        _trace_guard.active = False


def enable_function_tracing() -> None:
    """Enable global function-level tracing for the entire project."""

    global _function_tracing_enabled

    if _function_tracing_enabled:
        return

    sys.setprofile(_profile_function)
    threading.setprofile(_profile_function)
    _function_tracing_enabled = True

    structlog.get_logger("logging_setup").info(
        "Function-level tracing enabled",
        log_file=str(log_file_path),
    )


def setup_logging(*, enable_tracing: bool = False) -> None:
    """
    Configure structured logging for the entire application.

    This setup ensures that logs are consistently formatted and output
    to both the console and a rotating file. It handles logs from standard
    logging and structlog. When ``enable_tracing`` is True, every function call
    in the project is logged to ``seller_krypto.log``.
    """

    global _logging_configured

    if _logging_configured:
        if enable_tracing:
            enable_function_tracing()
        return

    log_level = settings.log_level.upper()

    # Define shared processors for structlog
    shared_processors: list[Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
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

    # Define formatter for file output (JSON for machine-readability)
    file_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )

    # Create console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)

    # Create file handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        log_file_path, maxBytes=5 * 1024 * 1024, backupCount=5  # 5 MB per file, 5 backups
    )
    file_handler.setFormatter(file_formatter)

    # Get the root logger and remove existing handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add our configured handlers
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    root_logger.setLevel(log_level)

    logger = structlog.get_logger("logging_setup")
    logger.info(
        "Logging configured successfully",
        level=log_level,
        file_path=str(log_file_path)
    )

    if enable_tracing:
        enable_function_tracing()

    _logging_configured = True
