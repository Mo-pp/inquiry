"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


def _parse_bool_env(value: str | None, default: bool) -> bool:
    """Parse a strict boolean environment value."""

    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"invalid boolean environment value: {value!r}")


def _parse_retry_delays(value: str | None) -> tuple[int, ...]:
    """Parse comma-separated positive retry delays in seconds."""

    if value is None or not value.strip():
        return (30, 120, 600)
    try:
        delays = tuple(int(item.strip()) for item in value.split(","))
    except ValueError as exc:
        raise ValueError("OUTREACH_RETRY_DELAYS_SECONDS must contain integers") from exc
    if not delays or any(delay <= 0 for delay in delays):
        raise ValueError("OUTREACH_RETRY_DELAYS_SECONDS must contain positive values")
    return delays


def _parse_optional_positive_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError("environment value must be an integer") from exc
    if parsed <= 0:
        raise ValueError("environment value must be positive")
    return parsed


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Runtime settings for the API, model provider, and MySQL."""

    model: str = DEFAULT_MODEL
    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    temperature: float = 0.0
    timeout: float = 30.0
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "lintratek_chat"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    google_spreadsheet_id: str | None = None
    google_sheet_gid: int = 0
    google_credentials_file: str = "credentials.json"
    google_token_file: str = "google-token.json"
    google_sheets_poll_interval_seconds: int = 60
    google_sheets_source: str = "api"
    google_apps_script_url: str | None = None
    google_apps_script_token: str | None = None

    # WhatsApp inbound ingestion is deliberately opt-in.  Keeping this off by
    # default means installing the phase-3 code cannot start processing a real
    # account until the operator explicitly enables it.
    whatsapp_inbound_enabled: bool = False
    whatsapp_inbox_lease_seconds: int = 300
    whatsapp_inbox_batch_size: int = 20

    # Local HTTP bridge used by the separately gated outreach executor.
    wa_bridge_url: str = "http://127.0.0.1:3010"
    wa_bridge_timeout_seconds: float = 10.0

    # Proactive outreach is a planning feature in phase 3.  A real sender is
    # never enabled by this setting alone; the final send phase will require a
    # separate explicit switch and an allowlist.
    outreach_dry_run: bool = True
    outreach_max_attempts: int = 3
    outreach_retry_delays_seconds: tuple[int, ...] = (30, 120, 600)
    outreach_daily_limit: int | None = None

    # Production lead ingestion is an explicit opt-in.  Keeping this disabled
    # by default prevents a fresh checkout (or a forgotten scheduler) from
    # reading the real form or writing rows to customer_leads.
    google_sheets_sync_enabled: bool = False

    @classmethod
    def from_env(cls) -> AppSettings:
        """Load the complete application configuration."""

        load_dotenv()
        return cls(
            model=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
            timeout=float(os.getenv("DEEPSEEK_TIMEOUT", "30")),
            mysql_host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
            mysql_user=os.getenv("MYSQL_USER", "root"),
            mysql_password=os.getenv("MYSQL_PASSWORD", ""),
            mysql_database=os.getenv("MYSQL_DATABASE", "lintratek_chat"),
            api_host=os.getenv("API_HOST", "127.0.0.1"),
            api_port=int(os.getenv("API_PORT", "8000")),
            google_spreadsheet_id=os.getenv("GOOGLE_SPREADSHEET_ID") or None,
            google_sheet_gid=int(os.getenv("GOOGLE_SHEET_GID", "0")),
            google_credentials_file=os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
            google_token_file=os.getenv("GOOGLE_TOKEN_FILE", "google-token.json"),
            google_sheets_poll_interval_seconds=int(
                os.getenv("GOOGLE_SHEETS_POLL_INTERVAL_SECONDS", "60")
            ),
            google_sheets_sync_enabled=_parse_bool_env(
                os.getenv("GOOGLE_SHEETS_SYNC_ENABLED"), False
            ),
            google_sheets_source=os.getenv("GOOGLE_SHEETS_SOURCE", "api").lower(),
            google_apps_script_url=os.getenv("GOOGLE_APPS_SCRIPT_URL") or None,
            google_apps_script_token=os.getenv("GOOGLE_APPS_SCRIPT_TOKEN") or None,
            whatsapp_inbound_enabled=_parse_bool_env(
                os.getenv("WHATSAPP_INBOUND_ENABLED"), False
            ),
            whatsapp_inbox_lease_seconds=int(
                os.getenv("WHATSAPP_INBOX_LEASE_SECONDS", "300")
            ),
            whatsapp_inbox_batch_size=int(
                os.getenv("WHATSAPP_INBOX_BATCH_SIZE", "20")
            ),
            wa_bridge_url=os.getenv("WA_BRIDGE_URL", "http://127.0.0.1:3010"),
            wa_bridge_timeout_seconds=float(
                os.getenv("WA_BRIDGE_TIMEOUT_SECONDS", "10")
            ),
            outreach_dry_run=_parse_bool_env(
                os.getenv("OUTREACH_DRY_RUN"), True
            ),
            outreach_max_attempts=int(
                os.getenv("OUTREACH_MAX_ATTEMPTS", "3")
            ),
            outreach_retry_delays_seconds=_parse_retry_delays(
                os.getenv("OUTREACH_RETRY_DELAYS_SECONDS", "30,120,600")
            ),
            outreach_daily_limit=_parse_optional_positive_int(
                os.getenv("OUTREACH_DAILY_LIMIT")
            ),
        )

    def mysql_connect_kwargs(self) -> dict[str, object]:
        """Return keyword arguments accepted by mysql.connector.connect."""

        return {
            "host": self.mysql_host,
            "port": self.mysql_port,
            "user": self.mysql_user,
            "password": self.mysql_password,
            "database": self.mysql_database,
            "charset": "utf8mb4",
        }
