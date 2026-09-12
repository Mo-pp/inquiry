"""将一条数据库线索拼成首次联系文本；不查库、不发送、不修改线索。"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any
import tomllib


TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "first_contact.toml"
ANSWER_FIELDS = (
    "business_role_answer",
    "target_country_answer",
    "coverage_area_answer",
    "purchase_purpose_answer",
    "frequency_budget_quantity_answer",
)


def build_first_contact_message(lead: Mapping[str, Any]) -> str:
    """接受 Repository 返回的字典，每次读取最新话术并返回纯文本。"""
    with TEMPLATE_PATH.open("rb") as file:
        config = tomllib.load(file)

    platform = str(lead.get("platform") or "").strip().lower()
    values = {
        "channel": config["channels"].get(platform, platform or config["unknown_channel"]),
    }
    for field in ANSWER_FIELDS:
        answer = lead.get(field)
        # 只清除首尾空白，保留答案内容、换行和花括号，不做二次格式化。
        values[field] = (
            str(answer).strip() if answer is not None else ""
        ) or config["missing_answer"]

    return config["message"].format(**values).strip()
