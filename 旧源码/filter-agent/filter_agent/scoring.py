"""Validation and rendering helpers for Claude's per-turn customer scoring."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

LevelCode = Literal["A", "B", "C", "D", "E", "K", "Z", "UNKNOWN"]


class ScorePoint(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    score: int


class ScoreEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rule_code: str = Field(min_length=1, max_length=64)
    message_key: str | None = Field(default=None, max_length=128)
    quote: str = Field(min_length=1, max_length=2000)


class ScoreDecision(BaseModel):
    """Machine-readable score returned in Claude's final CLI result."""

    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=0)
    score_delta: int
    level_code: LevelCode
    level_before: str | None = None
    matched_points: list[ScorePoint] = Field(default_factory=list)
    user_evidence: list[ScoreEvidence] = Field(default_factory=list)
    information_sufficient: bool = False


_MARKER_RE = re.compile(
    r"<SCORE_RESULT>\s*(?P<body>\{.*?\})\s*</SCORE_RESULT>",
    re.IGNORECASE | re.DOTALL,
)


class ScoreParseError(ValueError):
    pass


def parse_score_result(text: str) -> ScoreDecision:
    """Parse the tagged JSON emitted by Claude after it sends WhatsApp messages."""

    match = _MARKER_RE.search(text or "")
    if not match:
        raise ScoreParseError("Claude result does not contain <SCORE_RESULT> JSON")
    try:
        payload: Any = json.loads(match.group("body"))
        return ScoreDecision.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ScoreParseError("Claude score result is invalid") from exc


def render_score_message(decision: ScoreDecision, *, language: str = "en") -> str:
    """Render the customer-visible score message in the customer's language."""

    if language.strip().lower().startswith(("zh", "中文", "cn")):
        return _render_score_message_zh(decision)

    lines = [
        "Score result",
        "",
        f"Customer level: {decision.level_code}",
        f"Current score: {decision.score} points",
        "",
        "Matched scoring items:",
    ]
    if decision.matched_points:
        points = " + ".join(
            f"{point.name} ({point.score:+d} points)" for point in decision.matched_points
        )
        lines.append(points)
    else:
        lines.append("None")
    lines.extend(["", "Evidence from the customer:"])
    if decision.user_evidence:
        lines.extend(
            f"{index}. Customer: {evidence.quote}"
            for index, evidence in enumerate(decision.user_evidence, start=1)
        )
    else:
        lines.append("None")
    return "\n".join(lines)


def _render_score_message_zh(decision: ScoreDecision) -> str:
    """Render the legacy Chinese score format when the customer uses Chinese."""

    lines = [
        "评分结果",
        "",
        f"客户等级：{decision.level_code}",
        f"当前评分：{decision.score}分",
        "",
        "命中评分项：",
    ]
    if decision.matched_points:
        points = " + ".join(
            f"{point.name}（{point.score:+d}分）" for point in decision.matched_points
        )
        lines.append(points)
    else:
        lines.append("暂无")
    lines.extend(["", "原话证据："])
    if decision.user_evidence:
        lines.extend(
            f"{index}. 用户：{evidence.quote}"
            for index, evidence in enumerate(decision.user_evidence, start=1)
        )
    else:
        lines.append("暂无")
    return "\n".join(lines)
