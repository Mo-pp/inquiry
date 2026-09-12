"""读取并校验林创 AI 客服的统一配置。

非敏感配置来自项目根目录的 ``config.toml``，秘密来自同目录的
``.env``。本模块只负责配置，不会请求 Google、启动轮询或连接数据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import os
import re
import tomllib
from urllib.parse import urlparse

from dotenv import dotenv_values


# 无论程序从哪个工作目录启动，都以本文件所在项目为配置根目录。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


class SettingsError(ValueError):
    """配置文件缺失、字段错误或秘密未填写时抛出的异常。"""


@dataclass(frozen=True, slots=True)
class GoogleSheetsSettings:
    """Google 表格增量接收所需的完整配置。"""

    # Apps Script Web 应用地址和令牌来自 .env，不应写入 config.toml。
    apps_script_url: str
    apps_script_token: str = field(repr=False)

    # 以下普通配置来自 config.toml 的 [google_sheets]。
    gid: int
    poll_interval_seconds: float
    batch_size: int
    request_timeout_seconds: float
    enabled_on_startup: bool

    def safe_summary(self) -> dict[str, object]:
        """返回可用于启动日志的脱敏配置，绝不返回令牌内容。"""

        return {
            "apps_script_url": self.apps_script_url,
            "apps_script_token": "[configured]",
            "gid": self.gid,
            "poll_interval_seconds": self.poll_interval_seconds,
            "batch_size": self.batch_size,
            "request_timeout_seconds": self.request_timeout_seconds,
            "enabled_on_startup": self.enabled_on_startup,
        }


@dataclass(frozen=True, slots=True)
class MySQLSettings:
    host: str
    port: int
    user: str
    database: str
    password: str = field(repr=False)
    connection_timeout: int = 10

    def connect_kwargs(self) -> dict[str, object]:
        return dict(host=self.host, port=self.port, user=self.user,
                    database=self.database, password=self.password,
                    connection_timeout=self.connection_timeout, charset="utf8mb4",
                    autocommit=False, sql_mode="STRICT_ALL_TABLES")


@dataclass(frozen=True, slots=True)
class WaBridgeSettings:
    base_url: str = "http://127.0.0.1:3010"
    request_timeout_seconds: float = 60


@dataclass(frozen=True, slots=True)
class FirstContactSettings:
    enabled_on_startup: bool = False
    poll_interval_seconds: float = 20
    batch_size: int = 20


@dataclass(frozen=True, slots=True)
class Settings:
    """整个 Python 服务统一使用的配置对象。"""

    google_sheets: GoogleSheetsSettings
    mysql: MySQLSettings
    wa_bridge: WaBridgeSettings = field(default_factory=WaBridgeSettings)
    first_contact: FirstContactSettings = field(default_factory=FirstContactSettings)


def load_settings(
    config_path: Path = DEFAULT_CONFIG_PATH,
    env_path: Path = DEFAULT_ENV_PATH,
) -> Settings:
    """读取 TOML 与 .env，校验后返回不可变配置对象。

    已经存在于操作系统环境中的变量优先于 .env，方便部署环境覆盖本机文件。
    每次调用都会重新读取 TOML；正常服务只应在启动时调用一次。
    """

    if not config_path.is_file():
        raise SettingsError(f"配置文件不存在：{config_path}")

    # 读取文件而不修改进程环境，避免重复加载时旧值残留。
    # 不展开 ${...}，令牌中的字符按原值保留。仅识别下面两个环境变量。
    env_values = dotenv_values(dotenv_path=env_path, interpolate=False)

    try:
        with config_path.open("rb") as file:
            raw_config = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"config.toml 格式错误：{exc}") from exc

    google_config = raw_config.get("google_sheets")
    if not isinstance(google_config, dict):
        raise SettingsError("config.toml 缺少 [google_sheets] 配置段")

    url = _required_env("GOOGLE_APPS_SCRIPT_URL", env_values)
    enabled = _boolean(google_config, "enabled_on_startup")
    token = (os.environ.get("GOOGLE_APPS_SCRIPT_TOKEN", env_values.get("GOOGLE_APPS_SCRIPT_TOKEN")) or "").strip()
    _validate_apps_script_url(url)
    if (enabled or token) and len(token) < 32:
        raise SettingsError("GOOGLE_APPS_SCRIPT_TOKEN 至少需要 32 个字符")
    if token == "replace-with-a-long-random-secret":
        raise SettingsError("请替换 GOOGLE_APPS_SCRIPT_TOKEN 示例占位值")

    mysql_config = raw_config.get("mysql")
    if not isinstance(mysql_config, dict):
        raise SettingsError("config.toml 缺少 [mysql] 配置段")
    for name in ("host", "user", "database"):
        if not isinstance(mysql_config.get(name), str) or not mysql_config[name].strip():
            raise SettingsError(f"mysql.{name} 必须是非空字符串")
    for name, maximum in (("port", 65535), ("connection_timeout", 300)):
        value = mysql_config.get(name)
        if type(value) is not int or not 1 <= value <= maximum:
            raise SettingsError(f"mysql.{name} 必须是 1～{maximum} 的整数")
    password = os.environ.get("MYSQL_PASSWORD", env_values.get("MYSQL_PASSWORD")) or ""
    if enabled and not password:
        raise SettingsError("MYSQL_PASSWORD 未配置")

    bridge_config = raw_config.get("wa_bridge", {})
    if not isinstance(bridge_config, dict):
        raise SettingsError("wa_bridge 必须是配置段")
    bridge_url = bridge_config.get("base_url", "http://127.0.0.1:3010")
    if not isinstance(bridge_url, str):
        raise SettingsError("wa_bridge.base_url 必须是 HTTP(S) 地址")
    parsed_bridge = urlparse(bridge_url)
    if (parsed_bridge.scheme not in {"http", "https"} or not parsed_bridge.hostname
            or parsed_bridge.query or parsed_bridge.fragment or parsed_bridge.username):
        raise SettingsError("wa_bridge.base_url 必须是无查询参数或用户信息的 HTTP(S) 地址")
    bridge_timeout = bridge_config.get("request_timeout_seconds", 60)
    if (type(bridge_timeout) not in {int, float} or not 0 < bridge_timeout <= 300):
        raise SettingsError("wa_bridge.request_timeout_seconds 必须大于 0 且不超过 300")

    contact_config = raw_config.get("first_contact", {})
    if not isinstance(contact_config, dict):
        raise SettingsError("first_contact 必须是配置段")
    contact_enabled = contact_config.get("enabled_on_startup", False)
    contact_interval = contact_config.get("poll_interval_seconds", 20)
    contact_batch = contact_config.get("batch_size", 20)
    if type(contact_enabled) is not bool:
        raise SettingsError("first_contact.enabled_on_startup 必须是 true 或 false")
    if type(contact_interval) not in {int, float} or not 0 < contact_interval <= 86400:
        raise SettingsError("first_contact.poll_interval_seconds 必须大于 0 且不超过 86400")
    if type(contact_batch) is not int or not 1 <= contact_batch <= 500:
        raise SettingsError("first_contact.batch_size 必须为 1～500 的整数")

    return Settings(
        first_contact=FirstContactSettings(contact_enabled, contact_interval, contact_batch),
        wa_bridge=WaBridgeSettings(bridge_url.rstrip("/"), bridge_timeout),
        mysql=MySQLSettings(password=password, **mysql_config),
        google_sheets=GoogleSheetsSettings(
            apps_script_url=url,
            apps_script_token=token,
            gid=_integer(google_config, "gid", minimum=0),
            poll_interval_seconds=_number(
                google_config, "poll_interval_seconds", minimum_exclusive=0
            ),
            batch_size=_integer(
                google_config, "batch_size", minimum=1, maximum=500
            ),
            request_timeout_seconds=_number(
                google_config, "request_timeout_seconds", minimum_exclusive=0
            ),
            enabled_on_startup=_boolean(google_config, "enabled_on_startup"),
        )
    )


def _required_env(name: str, env_values: dict[str, str | None]) -> str:
    """读取必填环境变量，并拒绝空白值。"""

    value = (os.environ.get(name, env_values.get(name)) or "").strip()
    if not value:
        raise SettingsError(f"{name} 未配置")
    return value


def _validate_apps_script_url(value: str) -> None:
    """只接受正式 Apps Script HTTPS /exec 地址。"""

    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "script.google.com"
        or not re.fullmatch(r"/macros/s/[A-Za-z0-9_-]+/exec", parsed.path)
        or parsed.query
        or parsed.fragment
    ):
        raise SettingsError(
            "GOOGLE_APPS_SCRIPT_URL 必须是 script.google.com 上末尾为 /exec 的 HTTPS 地址"
        )
    if parsed.path == "/macros/s/DEPLOYMENT_ID/exec":
        raise SettingsError("请替换 GOOGLE_APPS_SCRIPT_URL 示例占位值")


def _integer(
    section: dict[str, object],
    name: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    """读取整数，注意 Python 的 bool 也是 int，因此必须显式排除。"""

    value = section.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(f"google_sheets.{name} 必须是整数")
    if value < minimum or (maximum is not None and value > maximum):
        range_text = f"{minimum}～{maximum}" if maximum is not None else f">= {minimum}"
        raise SettingsError(f"google_sheets.{name} 必须在 {range_text} 范围内")
    return value


def _number(
    section: dict[str, object],
    name: str,
    *,
    minimum_exclusive: float,
) -> float:
    """读取必须大于指定下限的秒数配置。"""

    value = section.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"google_sheets.{name} 必须是数字")
    try:
        result = float(value)
    except OverflowError as exc:
        raise SettingsError(f"google_sheets.{name} 数值过大") from exc
    if not math.isfinite(result) or result <= minimum_exclusive:
        raise SettingsError(f"google_sheets.{name} 必须大于 {minimum_exclusive}")
    return result


def _boolean(section: dict[str, object], name: str) -> bool:
    """严格读取 TOML 布尔值，不接受字符串形式的 true/false。"""

    value = section.get(name)
    if not isinstance(value, bool):
        raise SettingsError(f"google_sheets.{name} 必须是 true 或 false")
    return value
