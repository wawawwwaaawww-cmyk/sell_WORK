"""Simplified configuration for quick testing."""

import os
from typing import Optional


class Settings:
    """Simple settings class."""
    
    def __init__(self):
        # Telegram Bot
        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_webhook_url: str = os.getenv("TELEGRAM_WEBHOOK_URL", "")
        self.telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "webhook_secret")
        self.manager_channel_id: int = int(os.getenv("MANAGER_CHANNEL_ID", "0"))
        self.webhook_path: str = os.getenv("WEBHOOK_PATH", "/telegram/webhook")
        
        # Database
        self.database_url: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://seller_app:sellerapp_pass@localhost:5433/seller_krypto")
        
        # LLM
        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.llm_model: str = os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        
        # Security
        self.secret_key: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        self.hmac_secret: str = os.getenv("HMAC_SECRET", "your-hmac-secret")
        
        # Application
        self.debug: bool = os.getenv("DEBUG", "true").lower() == "true"
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO")


# Global settings instance
settings = Settings()