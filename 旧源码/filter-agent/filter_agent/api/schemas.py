"""HTTP response schemas."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictBool


class BatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["stored", "filtered"]
    is_relevant: StrictBool
    appended_count: int


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"

class CustomerExistsResponse(BaseModel):
    exists: bool


class InboundEventRequest(BaseModel):
    """JSON envelope emitted by the optional WAJS bridge forwarder.

    WAJS adds fields between versions, so unknown fields are retained rather
    than rejected.  The normalization layer applies the actual safety checks.
    """

    model_config = ConfigDict(extra="allow")

    event_type: str = "message"
    message_id: str | None = None
    chat_jid: str | None = None
    customer_phone: str | None = None
    sender: str | None = None
    text: str | None = None
    timestamp: Any = None
    from_me: bool | None = None
    is_group: bool | None = None


class InboundEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "duplicate", "ignored"]
    message_key: str | None = None
    job_id: int | None = None
    detail: str | None = None
