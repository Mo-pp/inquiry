"""接收状态仅驻留内存，重启或重新开启时不恢复旧游标。"""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RuntimeState:
    enabled: bool = False
    syncing: bool = False
    next_row: int | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    read_count: int = 0
    inserted_count: int = 0
    skipped_count: int = 0
