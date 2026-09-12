"""验证扫描开关、批次推进、停止和不确定发送；不联系真实客户。"""

from dataclasses import replace
from threading import Event, Thread
import unittest
from unittest.mock import Mock

from app.settings import FirstContactSettings
from app.leads.first_contact_service import FirstContactService, FirstContactResult
from app.messages.wa_bridge_client import SendOutcomeUnknown


class ScanTests(unittest.TestCase):
    def service(self, enabled=False):
        service = FirstContactService(Mock(), Mock(), FirstContactSettings(enabled, 20, 20))
        self.addCleanup(service.stop)
        return service

    def test_disabled_start_never_queries_or_sends(self):
        service = self.service()
        service.start()
        self.assertIsNone(service._thread)
        service.repository.list_uncontacted.assert_not_called()
        service.bridge.send_text.assert_not_called()

    def test_failed_and_unregistered_leads_do_not_starve_later_batches(self):
        service = self.service()
        service.repository.list_uncontacted.side_effect = [
            [{"source_lead_id": "a"}, {"source_lead_id": "b"}],
            [{"source_lead_id": "c"}], [], [{"source_lead_id": "a"}],
        ]
        service.process_one = Mock(side_effect=[ValueError("bad number"),
            FirstContactResult("not_registered", "b"), FirstContactResult("sent", "c"),
            FirstContactResult("not_registered", "a")])
        with self.assertLogs("app.leads.first_contact_service", level="INFO"):
            service.scan_once()
            service.scan_once()
            service.scan_once()
        self.assertEqual([call.args for call in service.repository.list_uncontacted.call_args_list],
                         [(20, None), (20, "b"), (20, "c"), (20,)])
        self.assertEqual([call.args[0] for call in service.process_one.call_args_list], ["a", "b", "c", "a"])

    def test_unknown_send_stops_remaining_batch_and_next_scan(self):
        service = self.service()
        service.repository.list_uncontacted.return_value = [{"source_lead_id": "a"}, {"source_lead_id": "b"}]
        service.process_one = Mock(side_effect=SendOutcomeUnknown("check manually"))
        with self.assertLogs("app.leads.first_contact_service", level="ERROR"):
            service.scan_once()
        service.scan_once()
        self.assertTrue(service._shutdown.is_set())
        self.assertIn("lead=a", service.blocked_reason)
        service.process_one.assert_called_once_with("a")
        service.repository.list_uncontacted.assert_called_once()

    def test_shutdown_waits_for_current_lead_and_skips_remaining_leads(self):
        service = self.service(True)
        entered, release, stopped = Event(), Event(), Event()
        service.repository.list_uncontacted.return_value = [{"source_lead_id": "a"}, {"source_lead_id": "b"}]
        def process(source_lead_id):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test release missing")
            return FirstContactResult("sent", source_lead_id)
        service.process_one = Mock(side_effect=process)
        service.start()
        self.assertTrue(entered.wait(2))
        worker = Thread(target=lambda: (service.stop(), stopped.set()))
        worker.start()
        try:
            self.assertFalse(stopped.wait(0.1))
        finally:
            release.set()
        worker.join(2)
        self.assertTrue(stopped.is_set())
        service.process_one.assert_called_once_with("a")

    def test_background_waits_after_round_and_can_wake_for_stop(self):
        service = self.service(True)
        ran = Event()
        service.scan_once = Mock(side_effect=ran.set)
        service.start()
        self.assertTrue(ran.wait(2))
        # 使用真实的 20 秒配置；停止应立即唤醒等待，不执行第二轮。
        service.stop()
        service.scan_once.assert_called_once()


if __name__ == "__main__":
    unittest.main()
