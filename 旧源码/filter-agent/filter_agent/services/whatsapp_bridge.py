"""Synchronous adapter for the local wa-bridge HTTP API.

Creating this adapter has no network side effects.  Calls happen only when a
future, explicitly gated outreach executor invokes ``check_number`` or
``send_text``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


class WhatsAppBridgeError(RuntimeError):
    """A bridge request failed before a reliable result was returned."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_kind: str = "temporary",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_kind = error_kind


class HttpWhatsAppBridge:
    """Implement the registration-checker and sender protocols."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:3010",
        timeout_seconds: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpWhatsAppBridge:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            response = self._client.post(f"{self.base_url}{path}", json=dict(payload))
        except httpx.TimeoutException as exc:
            raise WhatsAppBridgeError(
                "wa-bridge request timed out",
                error_kind="timeout",
            ) from exc
        except httpx.RequestError as exc:
            raise WhatsAppBridgeError(
                "wa-bridge request failed",
                error_kind="network",
            ) from exc

        try:
            data = response.json()
        except ValueError as exc:
            # Include enough transport detail to distinguish an old process,
            # a wrong route, and an HTML/proxy error page without logging an
            # unbounded response body.
            content_type = response.headers.get("content-type", "unknown")
            body = response.text.strip().replace("\r", " ").replace("\n", " ")
            if len(body) > 500:
                body = f"{body[:500]}..."
            detail = (
                f"wa-bridge returned invalid JSON (HTTP {response.status_code}, "
                f"content-type {content_type}, body={body!r})"
            )
            raise WhatsAppBridgeError(
                detail,
                status_code=response.status_code,
                error_kind="temporary",
            ) from exc
        if not isinstance(data, dict):
            raise WhatsAppBridgeError(
                "wa-bridge returned an invalid response",
                status_code=response.status_code,
                error_kind="temporary",
            )
        if response.status_code >= 400:
            error_kind = {
                429: "rate_limited",
                502: "temporary",
                503: "not_ready",
                504: "timeout",
            }.get(response.status_code, "non_retryable")
            raise WhatsAppBridgeError(
                str(data.get("detail") or f"wa-bridge returned HTTP {response.status_code}"),
                status_code=response.status_code,
                error_kind=error_kind,
            )
        return data

    def check_number(self, phone: str) -> dict[str, Any]:
        return self._post("/messages/check", {"phone": phone})

    def send_text(self, phone: str, text: str) -> dict[str, Any]:
        return self._post("/messages/send", {"phone": phone, "text": text})
