"""Configuration management for the Telegram Sales Bot."""

import os


class Settings:
    """Simple settings class."""

    def __init__(self) -> None:
        try:
            from dotenv import load_dotenv
            load_dotenv(dotenv_path='/home/botseller/.env')
        except ImportError:
            pass

        # Telegram Bot
        self.telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_webhook_url: str = os.getenv("TELEGRAM_WEBHOOK_URL", "")
        self.telegram_webhook_secret: str = os.getenv("TELEGRAM_WEBHOOK_SECRET", "webhook_secret")
        self.manager_channel_id: int = int(os.getenv("MANAGER_CHANNEL_ID", "0"))
        self.dialogs_channel_id: int = int(os.getenv("DIALOGS_CHANNEL_ID", "0"))
        self.webhook_path: str = os.getenv("WEBHOOK_PATH", "/telegram/webhook")

        # Database
        self.database_url: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://seller_app:sellerapp_pass@localhost:5433/seller_krypto")
        self.database_url_sync: str = os.getenv(
            "DATABASE_URL_SYNC",
            self._derive_sync_database_url(self.database_url),
        )

        # LLM
        self.openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")

        # Security
        self.secret_key: str = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
        self.hmac_secret: str = os.getenv("HMAC_SECRET", "your-hmac-secret")

        # Application
        self.debug: bool = os.getenv("DEBUG", "true").lower() == "true"
        self.log_level: str = os.getenv("LOG_LEVEL", "DEBUG")
        self.scheduler_timezone: str = os.getenv("SCHEDULER_TIMEZONE", "UTC")
        self.admin_ids: str = os.getenv("ADMIN_IDS", "")

        # Message history
        self.message_history_mode: str = os.getenv("MESSAGE_HISTORY_MODE", "preserve").lower()
        if self.message_history_mode not in {"preserve", "replace"}:
            self.message_history_mode = "preserve"
        self.conversation_logging_enabled: bool = os.getenv("CONVERSATION_LOGGING_ENABLED", "true").lower() == "true"

        # Rate limiting
        self.rate_limit_requests: int = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))
        self.rate_limit_window: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))

        # Timings
        self.bonus_followup_delay: int = int(os.getenv("BONUS_FOLLOWUP_DELAY", "3"))

        # Compatibility properties
        self.bot_token = self.telegram_bot_token
        self.webhook_url = self.telegram_webhook_url
        self.openai_model = self.llm_model

    def _derive_sync_database_url(self, database_url: str) -> str:
        """Derive a synchronous SQLAlchemy URL from an async one."""
        if not database_url:
            return ""

        if '+asyncpg' in database_url:
            return database_url.replace('+asyncpg', '')
        return database_url

    @property
    def admin_ids_list(self) -> list[int]:
        """Get admin IDs as list of integers."""
        if not self.admin_ids:
            return []
        return [int(uid.strip()) for uid in self.admin_ids.split(',') if uid.strip()]

    @property
    def allow_message_editing(self) -> bool:
        """Return True when bot may edit existing messages."""
        return self.message_history_mode == "replace"


settings = Settings()
