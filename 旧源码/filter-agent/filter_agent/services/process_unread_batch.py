"""Classify one unread batch and persist it when relevant."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from ..classification.models import UnreadBatch


class Classifier(Protocol):
    def classify(self, batch: UnreadBatch) -> bool: ...


class CustomerChatRepository(Protocol):
    def append_relevant(self, batch: UnreadBatch) -> int: ...


class ModelUnavailableError(RuntimeError):
    """Raised when the external language model cannot classify a batch."""


@dataclass(frozen=True, slots=True)
class ProcessResult:
    status: Literal["stored", "filtered"]
    is_relevant: bool
    appended_count: int


class ProcessUnreadBatch:
    def __init__(self, classifier: Classifier, repository: CustomerChatRepository) -> None:
        self._classifier = classifier
        self._repository = repository

    def process(self, batch: UnreadBatch) -> ProcessResult:
        try:
            is_relevant = self._classifier.classify(batch)
        except Exception as exc:
            raise ModelUnavailableError("The relevance model is unavailable") from exc

        if not is_relevant:
            return ProcessResult("filtered", False, 0)

        appended_count = self._repository.append_relevant(batch)
        return ProcessResult("stored", True, appended_count)
