"""单进程逐条首次联系；手动执行和后台扫描共用 process_one。"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from threading import Event, Thread

from app.leads.first_contact_template import build_first_contact_message
from app.messages.wa_bridge_client import normalize_phone, SendOutcomeUnknown
from app.settings import FirstContactSettings

LOGGER = logging.getLogger(__name__)


class ContactSaveError(RuntimeError):
    """发送成功但未能确认数据库提交，不能直接重新发送。"""


@dataclass(frozen=True, slots=True)
class FirstContactResult:
    status: str
    source_lead_id: str
    customer_id: int | None = None
    message_id: str | None = None


class FirstContactService:
    def __init__(self, repository, bridge, settings: FirstContactSettings | None = None):
        self.repository = repository
        self.bridge = bridge
        self.settings = settings or FirstContactSettings()
        self._shutdown = Event()
        self._thread = None
        self.blocked_reason = None
        self._after_lead_id = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        if not self.settings.enabled_on_startup:
            LOGGER.info("自动首次联系已关闭")
            return
        if self.blocked_reason:
            raise RuntimeError("首次联系已停止，需人工核对不确定的发送结果")
        self._shutdown.clear()
        self._thread = Thread(target=self._run, name="first-contact", daemon=True)
        self._thread.start()
        LOGGER.info("自动首次联系已开启，每轮结束后等待 %s 秒", self.settings.poll_interval_seconds)

    def stop(self):
        self._shutdown.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        LOGGER.info("首次联系扫描已停止，当前线索已处理结束")

    def scan_once(self):
        """单轮扫描；未知发送结果阻止后续线索和下一轮，不自动恢复。"""
        if self.blocked_reason:
            return
        leads = self.repository.list_uncontacted(self.settings.batch_size, self._after_lead_id)
        if not leads and self._after_lead_id is not None:
            self._after_lead_id = None
            leads = self.repository.list_uncontacted(self.settings.batch_size)
        for lead in leads:
            if self._shutdown.is_set():
                break
            source_lead_id = lead["source_lead_id"]
            # 跨轮向后扫描，防止前一批未注册/无效号码一直挡住后面的 lead。
            self._after_lead_id = source_lead_id
            try:
                result = self.process_one(source_lead_id)
                LOGGER.info("首次联系 lead=%s status=%s", source_lead_id, result.status)
            except (SendOutcomeUnknown, ContactSaveError) as exc:
                self.blocked_reason = f"lead={source_lead_id}: {exc}"
                self._shutdown.set()
                LOGGER.error("自动首次联系停止，需人工核对后再重启：%s", self.blocked_reason)
                break
            except Exception:
                LOGGER.exception("首次联系失败 lead=%s，本轮继续处理其他线索", source_lead_id)

    def _run(self):
        while not self._shutdown.is_set():
            try:
                self.scan_once()
            except Exception:
                LOGGER.exception("首次联系扫描查询失败，等待下一轮")
            self._shutdown.wait(self.settings.poll_interval_seconds)

    def process_one(self, source_lead_id: str) -> FirstContactResult:
        lead = self.repository.get_by_id(source_lead_id)
        if lead is None:
            raise ValueError(f"线索不存在：{source_lead_id}")
        if lead["customer_added"]:
            return FirstContactResult("already_contacted", source_lead_id, lead["customer_id"])
        phone = normalize_phone(lead.get("whatsapp_number"))
        text = build_first_contact_message(lead)
        # messages.body 为 MySQL TEXT；先检查可保存，避免发送后才发现过长。
        if not text or len(text.encode("utf-8")) > 65535:
            raise ValueError("首次联系消息不能为空，且 UTF-8 大小不能超过 65535 字节")
        checked = self.bridge.check_number(phone)
        if checked["status"] == "not_registered":
            return FirstContactResult("not_registered", source_lead_id)
        sent = self.bridge.send_text(phone, text)
        try:
            customer_id = self.repository.save_first_contact(
                source_lead_id=source_lead_id, phone=phone, chat_jid=sent.chat_jid,
                message_id=sent.message_id, body=text,
                sent_at=datetime.now(timezone.utc).replace(tzinfo=None),
            )
        except Exception as exc:
            raise ContactSaveError(
                f"消息已发送（message_id={sent.message_id}），但数据库保存未确认；"
                "请人工核对，勿直接重跑此 lead"
            ) from exc
        return FirstContactResult("sent", source_lead_id, customer_id, sent.message_id)
