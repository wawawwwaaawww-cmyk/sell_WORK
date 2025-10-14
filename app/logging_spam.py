"""Configuration for spam events logging."""

import logging
import sys

def setup_spam_logging():
    """
    Configures a dedicated logger for spam events.
    This allows routing spam logs to a separate file.
    """
    spam_logger = logging.getLogger("spam_events")
    spam_logger.setLevel(logging.INFO)

    # Prevent spam logs from propagating to the root logger
    spam_logger.propagate = False

    # Create a handler for the spam log file
    # In a real production environment, consider using RotatingFileHandler
    file_handler = logging.FileHandler("spam_events.log", mode='a', encoding='utf-8')
    
    # Create a formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(message)s'
    )
    file_handler.setFormatter(formatter)

    # Add the handler to the logger
    if not spam_logger.handlers:
        spam_logger.addHandler(file_handler)

    return spam_logger

# Initialize and get the logger instance
spam_events_logger = setup_spam_logging()