"""DeepSeek adapter used by the classifier."""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from ..config import AppSettings


def build_chat_model(settings: AppSettings) -> Any:
    kwargs: dict[str, Any] = {
        "model": settings.model,
        "temperature": settings.temperature,
        "max_retries": 0,
        "timeout": settings.timeout,
        "base_url": settings.base_url,
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    if settings.api_key:
        kwargs["api_key"] = settings.api_key
    return ChatOpenAI(**kwargs)
