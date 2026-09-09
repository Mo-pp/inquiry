"""单进程增量同步；构造对象不会联网或创建线程。"""
from dataclasses import replace
from datetime import datetime, timezone
from threading import Event, Lock, Thread
import logging

from app.settings import GoogleSheetsSettings
from .lead_mapper import map_row, EXPECTED_HEADERS
from .runtime_state import RuntimeState

LOGGER = logging.getLogger(__name__)


class LeadSyncService:
    def __init__(self, settings: GoogleSheetsSettings, gateway, repository):
        self.settings = settings
        self.gateway = gateway
        self.repository = repository
        self._state = RuntimeState()
        self._state_lock = Lock()
        self._operation_lock = Lock()
        self._control_lock = Lock()
        self._disabled = Event()
        self._disabled.set()
        self._shutdown = Event()
        self._thread = None

    @property
    def state(self) -> RuntimeState:
        with self._state_lock:
            return self._state

    def _update(self, **changes):
        with self._state_lock:
            self._state = replace(self._state, **changes)

    def enable(self):
        """成功取得末行后才开启；重复开启不跳过尚未处理的数据。"""
        with self._control_lock, self._operation_lock:
            if self.state.enabled:
                return self.state
            try:
                tail = self.gateway.get_tail()
                if tail.gid != self.settings.gid or tail.last_row < 1 or tail.next_row != tail.last_row + 1:
                    raise ValueError("网关末行响应不一致")
            except Exception as exc:
                self._update(last_error=type(exc).__name__ + ": 获取表格末行失败")
                raise
            self._update(enabled=True, next_row=tail.next_row, last_error=None)
            self._disabled.clear()
            return self.state

    def disable(self):
        """先禁止下一批，再等正在处理的一批结束；返回后可安全清库。"""
        with self._control_lock:
            self._disabled.set()
            self._update(enabled=False)
            with self._operation_lock:
                self._update(next_row=None)
            return self.state

    def sync_once(self):
        """处理当前新增数据，逐批提交；失败保留失败批次的起点并向调用方报错。"""
        with self._operation_lock:
            if self._disabled.is_set():
                return self.state
            self._update(syncing=True)
            try:
                while not self._disabled.is_set():
                    start = self.state.next_row
                    page = self.gateway.read_rows(start)
                    if (page.start_row != start or page.next_row != start + len(page.values)
                            or len(page.values) > self.settings.batch_size
                            or page.last_row < start - 1
                            or (page.values and page.next_row > page.last_row + 1)
                            or page.has_more != (page.next_row <= page.last_row)
                            or (page.has_more and not page.values)):
                        raise ValueError("网关分页进度不一致")
                    if [str(h).strip() for h in page.headers] != EXPECTED_HEADERS:
                        raise ValueError("表头不一致")
                    leads = [lead for row in page.values if (lead := map_row(page.headers, row)) is not None]
                    inserted = skipped = 0
                    if leads:
                        stats = self.repository.append(leads)
                        inserted, skipped = stats.inserted, stats.skipped
                    previous = self.state
                    self._update(
                        next_row=page.next_row, last_success_at=datetime.now(timezone.utc), last_error=None,
                        read_count=previous.read_count + len(page.values),
                        inserted_count=previous.inserted_count + inserted,
                        skipped_count=previous.skipped_count + skipped + len(page.values) - len(leads),
                    )
                    if not page.has_more:
                        break
                LOGGER.info("Google 同步完成：累计新增 %s，跳过 %s，下一行 %s",
                            self.state.inserted_count, self.state.skipped_count, self.state.next_row)
            except Exception as exc:
                # 状态不记录客户正文、令牌或数据库驱动可能包含的数据。
                self._update(last_error=type(exc).__name__ + ": 同步失败，保留当前行号待重试")
                LOGGER.error("%s", self.state.last_error)
                raise
            finally:
                self._update(syncing=False)
            return self.state

    def start(self):
        """按启动时加载的配置运行；不监听配置文件，不提供 HTTP 开关。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._shutdown.clear()
        if not self.settings.enabled_on_startup:
            self.disable()
            LOGGER.info("Google 表格接收已关闭；修改配置后需重启服务")
            return
        # 取得末行失败时直接报告启动失败，不能误称已开启。
        self.enable()
        LOGGER.info("Google 表格接收已开启，从第 %s 行开始", self.state.next_row)
        self._thread = Thread(target=self._run, name="lead-sync", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._shutdown.is_set():
            try:
                self.sync_once()
            except Exception:
                pass  # 错误已保存到状态，下一个轮询周期重试。
            self._shutdown.wait(self.settings.poll_interval_seconds)

    def stop(self):
        """由服务退出流程调用；唤醒定时等待，并等待当前批次结束。"""
        self._shutdown.set()
        self.disable()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        LOGGER.info("Google 表格接收已停止，当前批次已结束")
