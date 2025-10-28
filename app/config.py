"""Configuration management for the Telegram Sales Bot."""

import os


class Settings:
    """Simple settings class."""

    def __init__(self) -> None:
        try:
            from dotenv import load_dotenv
            load_dotenv()
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
        self.db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "50"))
        self.db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "10"))

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

        # Redis
        self.redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

        # Anti-spam
        self.spam_enabled: bool = os.getenv("SPAM_ENABLED", "true").lower() == "true"
        self.spam_threshold_burst10: int = int(os.getenv("SPAM_THRESHOLD_BURST10", "8"))
        self.spam_threshold_minute60: int = int(os.getenv("SPAM_THRESHOLD_MINUTE60", "20"))
        self.spam_threshold_dupe30: int = int(os.getenv("SPAM_THRESHOLD_DUPE30", "5"))
        self.spam_ban_base_hours: int = int(os.getenv("SPAM_BAN_BASE_HOURS", "2"))
        self.spam_ban_multiplier: int = int(os.getenv("SPAM_BAN_MULTIPLIER", "2"))
        self.spam_ban_max_hours: int = int(os.getenv("SPAM_BAN_MAX_HOURS", "24"))
        self.spam_decay_days: int = int(os.getenv("SPAM_DECAY_DAYS", "14"))

        # Message history
        self.message_history_mode: str = os.getenv("MESSAGE_HISTORY_MODE", "preserve").lower()
        if self.message_history_mode not in {"preserve", "replace"}:
            self.message_history_mode = "preserve"
        self.conversation_logging_enabled: bool = os.getenv("CONVERSATION_LOGGING_ENABLED", "true").lower() == "true"

        # Rate limiting
        self.rate_limit_requests: int = int(os.getenv("RATE_LIMIT_REQUESTS", "5"))
        self.rate_limit_window: int = int(os.getenv("RATE_LIMIT_WINDOW", "10"))

        # Timings
        self.bonus_followup_delay: int = int(os.getenv("BONUS_FOLLOWUP_DELAY", "3"))

        # Sendto command settings
        self.sendto_max_recipients: int = int(os.getenv("SENDTO_MAX_RECIPIENTS", "50"))
        self.sendto_throttle_rate: float = float(os.getenv("SENDTO_THROTTLE_RATE", "0.05"))
        self.sendto_cooldown_seconds: int = int(os.getenv("SENDTO_COOLDOWN_SECONDS", "5"))
        
        # Compatibility properties
        self.bot_token = self.telegram_bot_token
        self.webhook_url = self.telegram_webhook_url
        self.openai_model = self.llm_model

        # Script settings
        self.scripts_enabled: bool = os.getenv("SCRIPTS_ENABLED", "true").lower() == "true"
        self.scripts_index_path: str = os.getenv("SCRIPTS_INDEX_PATH", "/home/botseller/sell/data/sell_scripts.xlsx")
        self.retrieval_top_k: int = int(os.getenv("RETRIEVAL_TOP_K", "5"))
        self.retrieval_threshold: float = float(os.getenv("RETRIEVAL_THRESHOLD", "0.78"))
        self.retrieval_strong_hit: float = float(os.getenv("RETRIEVAL_STRONG_HIT", "0.85"))
        self.retrieval_delta_margin: float = float(os.getenv("RETRIEVAL_DELTA_MARGIN", "0.05"))
        self.judge_model: str = os.getenv("JUDGE_MODEL", self.llm_model)
        self.judge_max_candidates: int = int(os.getenv("JUDGE_MAX_CANDIDATES", "3"))

        # Sales script settings
        self.sales_script_enabled: bool = os.getenv("SALES_SCRIPT_ENABLED", "true").lower() == "true"
        self.sales_script_prompt_path: str = os.getenv(
            "SALES_SCRIPT_PROMPT_PATH",
            "/home/botseller/sell/prompts/sell-skript.txt",
        )
        self.sales_script_model: str = os.getenv("SALES_SCRIPT_MODEL", "gpt-4o-mini")
        self.sales_script_temperature: float = float(os.getenv("SALES_SCRIPT_TEMPERATURE", "0.3"))
        self.sales_script_max_tokens: int = int(os.getenv("SALES_SCRIPT_MAX_TOKENS", "3000"))
        self.sales_script_send_to_manager_on_assign: bool = (
            os.getenv("SALES_SCRIPT_SEND_TO_MANAGER_ON_ASSIGN", "true").lower() == "true"
        )
        self.sales_script_thread_post_on_click: bool = (
            os.getenv("SALES_SCRIPT_THREAD_POST_ON_CLICK", "true").lower() == "true"
        )
        self.sales_script_regen_on_lead_update: bool = (
            os.getenv("SALES_SCRIPT_REGEN_ON_LEAD_UPDATE", "true").lower() == "true"
        )
        self.sales_script_split_long_messages: bool = (
            os.getenv("SALES_SCRIPT_SPLIT_LONG_MESSAGES", "true").lower() == "true"
        )

        # Re-ask functionality
        self.reask_enabled: bool = os.getenv("REASK_ENABLED", "true").lower() == "true"
        self.reask_first_cooldown_min: int = int(os.getenv("REASK_FIRST_COOLDOWN_MIN", "4"))
        self.reask_second_cooldown_min: int = int(os.getenv("REASK_SECOND_COOLDOWN_MIN", "15"))
        self.reask_min_user_turns: int = int(os.getenv("REASK_MIN_USER_TURNS", "2"))
        self.reask_max_attempts: int = int(os.getenv("REASK_MAX_ATTEMPTS", "2"))

        # Anti-loop mechanism
        self.anti_loop_min_minutes_between_same_question: int = int(os.getenv("ANTI_LOOP_MIN_MINUTES_BETWEEN_SAME_QUESTION", "10"))
        self.anti_loop_min_user_msgs_between_same_question: int = int(os.getenv("ANTI_LOOP_MIN_USER_MSGS_BETWEEN_SAME_QUESTION", "3"))

        # Incomplete Leads
        self.incomplete_leads_wait_minutes: int = int(os.getenv("INCOMPLETE_LEADS_WAIT_MINUTES", "10"))
        self.incomplete_leads_extend_on_activity: bool = os.getenv("INCOMPLETE_LEADS_EXTEND_ON_ACTIVITY", "false").lower() == "true"
        self.incomplete_leads_admin_channel_id: int = int(os.getenv("INCOMPLETE_LEADS_ADMIN_CHANNEL_ID", str(self.manager_channel_id)))
        self.incomplete_leads_show_last_user_msgs: int = int(os.getenv("INCOMPLETE_LEADS_SHOW_LAST_USER_MSGS", "2"))
        
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
