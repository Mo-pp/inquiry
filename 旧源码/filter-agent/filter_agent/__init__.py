"""Public package interface for unread-message relevance filtering."""

from .classification.classifier import RelevanceClassifier
from .classification.models import ChatMessage, RelevanceDecision, UnreadBatch
from .config import AppSettings

__all__ = [
    "AppSettings",
    "ChatMessage",
    "RelevanceClassifier",
    "RelevanceDecision",
    "UnreadBatch",
]
