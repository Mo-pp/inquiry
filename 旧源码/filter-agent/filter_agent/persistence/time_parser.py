"""Parse WhatsApp timestamps without substituting the current time."""
from __future__ import annotations
from datetime import datetime, timezone
import re

class MessageTimeError(ValueError):
    pass

_PATTERNS = ("%H:%M, %Y/%m/%d", "%H:%M, %Y-%m-%d", "%H:%M, %d/%m/%Y", "%H:%M, %m/%d/%Y", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M")

def parse_message_time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    # Service-side WAJS events use ISO-8601 while the legacy extension sends a
    # human-readable local timestamp.  Accept both representations and store
    # timezone-aware values consistently as naive UTC (matching the existing
    # MySQL DATETIME columns).
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        pass
    text = re.sub(
        r"\s+",
        " ",
        str(value).replace("年", "/").replace("月", "/").replace("日", ""),
    ).strip()
    for pattern in _PATTERNS:
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    raise MessageTimeError(f"无法解析消息时间: {value}")
