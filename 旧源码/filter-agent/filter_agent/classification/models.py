"""Validated data exchanged by the API and classifier."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
)

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Direction = Literal["in", "out"]
PHONE_PATTERN = re.compile(r"^\+[1-9]\d{5,14}$")


class ChatMessage(BaseModel):
    """One normalized WhatsApp message."""

    model_config = ConfigDict(extra="forbid")

    message_key: NonEmptyText
    sender: NonEmptyText
    direction: Direction
    timestamp: NonEmptyText
    text: NonEmptyText


class UnreadBatch(BaseModel):
    """One unread customer batch with limited preceding context."""

    model_config = ConfigDict(extra="forbid")

    customer_name: NonEmptyText
    customer_phone: NonEmptyText
    history_complete: bool = False
    all_messages: list[ChatMessage] = Field(default_factory=list)
    context_messages: list[ChatMessage] = Field(default_factory=list, max_length=5)
    unread_messages: list[ChatMessage] = Field(min_length=1)

    @field_validator("customer_phone")
    @classmethod
    def validate_customer_phone(cls, value: str) -> str:
        if not PHONE_PATTERN.fullmatch(value):
            raise ValueError(
                "customer_phone must be an international number such as +8613800000000"
            )
        return value

    @field_validator("all_messages")
    @classmethod
    def validate_history_messages(cls, value: list[ChatMessage], info) -> list[ChatMessage]:
        if info.data.get("history_complete", False) and not value:
            raise ValueError("all_messages is required when history_complete is true")
        if not info.data.get("history_complete", False) and value:
            raise ValueError("all_messages is only allowed for complete history batches")
        return value

    @field_validator("unread_messages")
    @classmethod
    def validate_unread_directions(cls, value: list[ChatMessage]) -> list[ChatMessage]:
        if any(message.direction != "in" for message in value):
            raise ValueError("unread_messages may only contain incoming customer messages")
        return value


class RelevanceDecision(BaseModel):
    """Strict structured output returned by the model."""

    model_config = ConfigDict(extra="forbid")

    is_relevant: StrictBool
