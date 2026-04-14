"""
config.py
---------
Central configuration loader. All settings come from environment variables.
No hardcoded secrets or per-brand constants live here.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # --- Core ---
    PORT: int = int(os.environ.get("PORT", 8080))
    ENVIRONMENT: str = os.environ.get("ENVIRONMENT", "development")
    DARK_LAUNCH_MODE: bool = os.environ.get("DARK_LAUNCH_MODE", "true").lower() == "true"
    POLL_INTERVAL_MINUTES: int = int(os.environ.get("POLL_INTERVAL_MINUTES", 480))
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()

    # --- Database ---
    DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

    # --- YouTube ---
    YOUTUBE_API_KEY: str = os.environ.get("YOUTUBE_API_KEY", "")

    # --- OpenAI (Phase 2+) ---
    OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")

    # --- Buzzsprout (Phase 2+) ---
    BUZZSPROUT_API_TOKEN: str = os.environ.get("BUZZSPROUT_API_TOKEN", "")

    # --- ClickUp ---
    CLICKUP_API_TOKEN: str = os.environ.get("CLICKUP_API_TOKEN", "")
    CLICKUP_APPROVAL_QUEUE_LIST_ID: str = os.environ.get(
        "CLICKUP_APPROVAL_QUEUE_LIST_ID", "901816948630"
    )

    # --- Google Drive (Phase 2+) ---
    GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_B64: str = os.environ.get(
        "GOOGLE_DRIVE_SERVICE_ACCOUNT_JSON_B64", ""
    )

    # --- Email Notifications ---
    NOTIFICATION_EMAIL_TO: str = os.environ.get("NOTIFICATION_EMAIL_TO", "")
    NOTIFICATION_EMAIL_FROM: str = os.environ.get("NOTIFICATION_EMAIL_FROM", "")
    GMAIL_APP_PASSWORD: str = os.environ.get("GMAIL_APP_PASSWORD", "")

    @classmethod
    def validate_phase1(cls) -> list[str]:
        """
        Returns a list of missing required env vars for Phase 1.
        Call at startup; log warnings for each missing var.
        """
        required = {
            "DATABASE_URL": cls.DATABASE_URL,
            "YOUTUBE_API_KEY": cls.YOUTUBE_API_KEY,
        }
        return [k for k, v in required.items() if not v]

    @classmethod
    def is_production(cls) -> bool:
        return cls.ENVIRONMENT == "production"

    @classmethod
    def redacted_summary(cls) -> dict:
        """
        Returns a log-safe config summary with secrets redacted.
        Never log actual secret values.
        """
        def mask(val: str) -> str:
            if not val:
                return "<NOT SET>"
            return val[:4] + "****" if len(val) > 4 else "****"

        return {
            "environment": cls.ENVIRONMENT,
            "dark_launch_mode": cls.DARK_LAUNCH_MODE,
            "poll_interval_minutes": cls.POLL_INTERVAL_MINUTES,
            "database_url": mask(cls.DATABASE_URL),
            "youtube_api_key": mask(cls.YOUTUBE_API_KEY),
            "openai_api_key": mask(cls.OPENAI_API_KEY),
            "buzzsprout_api_token": mask(cls.BUZZSPROUT_API_TOKEN),
            "clickup_api_token": mask(cls.CLICKUP_API_TOKEN),
            "gmail_app_password": mask(cls.GMAIL_APP_PASSWORD),
        }
