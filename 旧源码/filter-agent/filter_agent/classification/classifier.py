"""LangChain application service for business relevance classification."""

from __future__ import annotations

from typing import Any

from ..config import AppSettings
from .llm import build_chat_model
from .models import RelevanceDecision, UnreadBatch
from .prompts import build_relevance_prompt
from .serialization import serialize_unread_batch


class RelevanceClassifier:
    """A stateless, tool-free classifier with strict structured output."""

    def __init__(self, *, settings: AppSettings | None = None, llm: Any | None = None) -> None:
        self.settings = settings or AppSettings.from_env()
        chat_model = llm or build_chat_model(self.settings)
        structured_model = chat_model.with_structured_output(
            RelevanceDecision,
            method="function_calling",
        )
        self.chain = build_relevance_prompt() | structured_model

    def invoke(self, batch: UnreadBatch) -> RelevanceDecision:
        result = self.chain.invoke({"chat_data": serialize_unread_batch(batch)})
        if isinstance(result, RelevanceDecision):
            return result
        return RelevanceDecision.model_validate(result)

    def classify(self, batch: UnreadBatch) -> bool:
        return self.invoke(batch).is_relevant
