"""Serialize chat data without granting it LangChain message authority."""

from __future__ import annotations

import json

from .models import UnreadBatch


def serialize_unread_batch(batch: UnreadBatch) -> str:
    payload = {
        "context_messages": [message.model_dump() for message in batch.context_messages],
        "unread_messages": [message.model_dump() for message in batch.unread_messages],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
