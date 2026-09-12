"""调用 WhatsApp 桥；不重试发送，不访问数据库。"""

from dataclasses import dataclass
import re

import httpx

from app.settings import WaBridgeSettings


class BridgeRequestError(RuntimeError):
    """请求被拒绝或号码检查失败。"""


class SendOutcomeUnknown(RuntimeError):
    """消息可能已发送，应先人工核对，不能直接重发。"""


@dataclass(frozen=True, slots=True)
class SendResult:
    phone: str
    chat_jid: str
    message_id: str


def normalize_phone(value: str | None) -> str:
    """清理常见格式字符，但不猜国家代码，也不修改原始 lead。"""
    phone = re.sub(r"[\s()\-]", "", value or "")
    if not re.fullmatch(r"\+[1-9][0-9]{5,14}", phone):
        raise ValueError("WhatsApp 号码必须包含 + 和国家区号，例如 +8613800000000")
    return phone


def _chat_id(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9]+@(c\.us|lid)", value) is not None


class WaBridgeClient:
    def __init__(self, settings: WaBridgeSettings, *, transport=None):
        self.http = httpx.Client(
            base_url=settings.base_url, timeout=settings.request_timeout_seconds,
            transport=transport, trust_env=False,
        )

    def check_number(self, phone: str) -> dict:
        phone = normalize_phone(phone)
        try:
            response = self.http.post("/messages/check", json={"phone": phone})
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BridgeRequestError("WhatsApp 号码检查失败") from exc
        if (not isinstance(data, dict) or data.get("phone") != phone
                or data.get("status") not in {"registered", "not_registered"}
                or (data["status"] == "registered" and not _chat_id(data.get("chat_jid")))
                or (data["status"] == "not_registered" and data.get("chat_jid") is not None)):
            raise BridgeRequestError("WhatsApp 号码检查响应无效")
        return data

    def send_text(self, phone: str, text: str) -> SendResult:
        phone = normalize_phone(phone)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("消息正文不能为空")
        try:
            response = self.http.post("/messages/send", json={"phone": phone, "text": text})
        except httpx.RequestError as exc:
            raise SendOutcomeUnknown("发送请求断开或超时，请人工核对 WhatsApp，勿直接重发") from exc
        if response.status_code in {400, 404, 413, 503}:
            raise BridgeRequestError(f"WhatsApp 桥拒绝发送，HTTP {response.status_code}")
        try:
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SendOutcomeUnknown("发送响应无效，请人工核对 WhatsApp，勿直接重发") from exc
        if (not isinstance(data, dict) or data.get("status") != "sent"
                or data.get("phone") != phone or not _chat_id(data.get("chat_jid"))
                or not isinstance(data.get("message_id"), str) or not data["message_id"].strip()
                or len(data["message_id"]) > 128 or len(data["chat_jid"]) > 64):
            raise SendOutcomeUnknown("未取得完整发送结果，请人工核对 WhatsApp，勿直接重发")
        return SendResult(phone, data["chat_jid"], data["message_id"])

    def close(self):
        self.http.close()
