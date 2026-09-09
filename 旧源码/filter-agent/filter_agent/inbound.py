"""Normalization helpers for messages received from whatsapp-web.js.

The browser extension used to build ``UnreadBatch`` objects by inspecting the
WhatsApp Web DOM.  The service path receives a small JSON envelope from the
WAJS bridge instead.  This module deliberately contains no network or database
side effects: it only validates and normalizes an event so it can safely be
persisted by :mod:`filter_agent.persistence.inbox_repository`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping

from .classification.models import ChatMessage, UnreadBatch


_E164 = re.compile(r"^\+[1-9]\d{5,14}$")
_JID_PHONE = re.compile(r"^(\d{6,15})@c\.us$")


class InboundMessageError(ValueError):
    """Raised when a WAJS event cannot be normalized safely."""


@dataclass(frozen=True, slots=True)
class InboundEnvelope:
    """A normalized incoming WhatsApp message ready for durable storage."""

    message_key: str
    wa_message_id: str | None
    chat_jid: str
    customer_phone: str | None
    sender: str
    direction: str
    message_at: datetime
    text: str
    is_group: bool
    raw_payload: dict[str, Any]

    def as_chat_message(self) -> ChatMessage:
        """Convert the envelope to the existing filter-agent message shape."""

        return ChatMessage(
            message_key=self.message_key,
            sender=self.sender,
            direction="in",
            timestamp=self.message_at.isoformat(),
            text=self.text,
        )

    def as_unread_batch(self, *, customer_name: str | None = None) -> UnreadBatch:
        """Build the compatibility payload consumed by the current classifier.

        A phone number is required by the legacy ``UnreadBatch`` contract.  A
        LID-only event is therefore persisted first and left for identity
        resolution/manual review rather than assigning a fabricated number.
        """

        if not self.customer_phone:
            raise InboundMessageError(
                "cannot build an UnreadBatch until the chat has a phone mapping"
            )
        return UnreadBatch(
            customer_name=(customer_name or self.sender or self.customer_phone),
            customer_phone=self.customer_phone,
            context_messages=[],
            unread_messages=[self.as_chat_message()],
        )


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def normalize_phone(value: Any, *, allow_bare: bool = False) -> str | None:
    """Normalize a phone value to ``+`` E.164 without guessing a country.

    WAJS commonly exposes contact numbers as digits without punctuation.  We
    accept the usual separators and an international ``00`` prefix. A local
    number without a country code is rejected unless the caller explicitly
    passes ``allow_bare=True`` for a source that guarantees international
    digits (such as WAJS ``Contact.number``).
    """

    text = _clean_text(value)
    if not text:
        return None
    if text.lower().startswith("p:"):
        text = text[2:].strip()
    if text.startswith("00"):
        text = "+" + text[2:]
    if not text.startswith("+"):
        # A bare number has no country context and is rejected by default.
        # The WAJS adapter may opt in to this for contact.number, which is
        # already an international WhatsApp identifier even when serialized
        # without a leading plus.
        if not allow_bare or not text.isdigit() or not 6 <= len(text) <= 15:
            return None
        text = "+" + text
    digits = "+" + "".join(character for character in text[1:] if character.isdigit())
    return digits if _E164.fullmatch(digits) else None


def _jid_phone(value: Any) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    match = _JID_PHONE.fullmatch(text)
    return f"+{match.group(1)}" if match else None


def _payload_value(payload: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return None


def _nested_serialized(value: Any) -> str | None:
    if isinstance(value, Mapping):
        return _clean_text(value.get("_serialized") or value.get("serialized"))
    return _clean_text(value)


def _message_id(payload: Mapping[str, Any]) -> str | None:
    direct = _clean_text(_payload_value(payload, "message_id", "wa_message_id"))
    if direct:
        return direct
    nested = payload.get("id")
    return _nested_serialized(nested)


def _chat_jid(payload: Mapping[str, Any]) -> str | None:
    direct = _clean_text(_payload_value(payload, "chat_jid", "chat_id", "from"))
    if direct:
        return direct
    return _nested_serialized(payload.get("chat"))


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        # whatsapp-web.js exposes Unix seconds.  A millisecond value is also
        # accepted for integrations that serialize Date.now().
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
    else:
        text = _clean_text(value)
        if not text:
            raise InboundMessageError("message timestamp is required")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise InboundMessageError("message timestamp is invalid") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _stable_message_key(
    *,
    message_id: str | None,
    chat_jid: str,
    timestamp: datetime,
    sender: str,
    text: str,
) -> str:
    source = message_id or "\x1f".join(
        [chat_jid, timestamp.isoformat(), sender, text]
    )
    return hashlib.sha256(f"wa:{source}".encode("utf-8")).hexdigest()


def normalize_inbound_event(payload: Mapping[str, Any]) -> InboundEnvelope | None:
    """Normalize one WAJS JSON event.

    ``None`` is returned for self-authored messages and group chats.  Those
    events are intentionally ignored before they can enter the customer queue.
    """

    if not isinstance(payload, Mapping):
        raise InboundMessageError("inbound event must be a JSON object")

    if _truthy(_payload_value(payload, "from_me", "fromMe")):
        return None

    chat_jid = _chat_jid(payload)
    if not chat_jid:
        raise InboundMessageError("chat_jid is required")

    chat_payload = payload.get("chat")
    nested_group = (
        isinstance(chat_payload, Mapping)
        and _truthy(chat_payload.get("isGroup") or chat_payload.get("is_group"))
    )
    is_group = (
        _truthy(_payload_value(payload, "is_group", "isGroup"))
        or nested_group
        or chat_jid.endswith("@g.us")
    )
    if (
        is_group
        or chat_jid.endswith("@broadcast")
        or chat_jid.endswith("@newsletter")
        or chat_jid == "status@broadcast"
    ):
        return None

    message_id = _message_id(payload)
    message_at = _timestamp(
        _payload_value(payload, "timestamp", "message_at", "t")
    )
    sender = _clean_text(
        _payload_value(
            payload,
            "sender",
            "sender_name",
            "pushname",
            "notify_name",
            "notifyName",
        )
    ) or chat_jid
    text = _clean_text(_payload_value(payload, "text", "body", "caption"))
    if not text:
        message_type = _clean_text(_payload_value(payload, "type", "message_type"))
        text = f"[{message_type or 'non-text'} message]"

    customer_phone = normalize_phone(
        _payload_value(
            payload,
            "customer_phone",
            "phone",
            "contact_number",
            "number",
        )
    )
    if customer_phone is None:
        customer_phone = _jid_phone(chat_jid)
    if customer_phone is None and isinstance(chat_payload, Mapping):
        contact_id = _nested_serialized(chat_payload.get("id"))
        customer_phone = _jid_phone(contact_id)

    supplied_key = _clean_text(_payload_value(payload, "message_key"))
    if supplied_key and re.fullmatch(r"[0-9a-fA-F]{64}", supplied_key):
        # The bridge computes the same key before forwarding.  Preserve it so
        # retries across the HTTP boundary remain exactly idempotent.
        message_key = supplied_key.lower()
    else:
        message_key = _stable_message_key(
            message_id=message_id,
            chat_jid=chat_jid,
            timestamp=message_at,
            sender=sender,
            text=text,
        )

    # Make a JSON-safe copy.  The HTTP boundary already supplies JSON, but
    # this also keeps the helper safe when called directly by tests/workers.
    try:
        raw_payload = json.loads(json.dumps(dict(payload), ensure_ascii=False, default=str))
    except (TypeError, ValueError) as exc:
        raise InboundMessageError("inbound event contains non-serializable data") from exc

    return InboundEnvelope(
        message_key=message_key,
        wa_message_id=message_id,
        chat_jid=chat_jid,
        customer_phone=customer_phone,
        sender=sender,
        direction="in",
        message_at=message_at,
        text=text,
        is_group=False,
        raw_payload=raw_payload,
    )
