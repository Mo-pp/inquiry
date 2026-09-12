"""只模拟 HTTP 和数据库；不向真实 WhatsApp 发送。"""

import json
import unittest
from unittest.mock import Mock

import httpx

from app.settings import WaBridgeSettings
from app.messages.wa_bridge_client import WaBridgeClient, BridgeRequestError, SendOutcomeUnknown
from app.leads.first_contact_service import FirstContactService, ContactSaveError


class FirstContactTests(unittest.TestCase):
    def setup_service(self, *, registered=True, send_response=None, send_error=None):
        self.requests = []
        def handler(request):
            self.requests.append((request.url.path, json.loads(request.content)))
            phone = json.loads(request.content)["phone"]
            if request.url.path == "/messages/check":
                return httpx.Response(200, json={"phone": phone,
                    "status": "registered" if registered else "not_registered",
                    "chat_jid": "123456@lid" if registered else None})
            if send_error:
                raise send_error
            return send_response if send_response is not None else httpx.Response(200, json={
                "status": "sent", "phone": phone, "chat_jid": "123456@lid", "message_id": "msg-1"})
        self.bridge = WaBridgeClient(WaBridgeSettings(), transport=httpx.MockTransport(handler))
        self.addCleanup(self.bridge.close)
        self.repository = Mock()
        self.lead = {"source_lead_id": "lead-1", "customer_added": False, "customer_id": None,
                     "whatsapp_number": "+86 138-0000-0000", "platform": "fb"}
        self.repository.get_by_id.return_value = self.lead
        self.repository.save_first_contact.return_value = 42
        return FirstContactService(self.repository, self.bridge)

    def test_full_flow_normalizes_phone_and_saves_exact_sent_text(self):
        service = self.setup_service()
        result = service.process_one("lead-1")
        self.assertEqual(result.status, "sent")
        self.assertEqual(result.customer_id, 42)
        self.assertEqual([r[0] for r in self.requests], ["/messages/check", "/messages/send"])
        saved = self.repository.save_first_contact.call_args.kwargs
        self.assertEqual(saved["phone"], "+8613800000000")
        self.assertEqual(saved["body"], self.requests[1][1]["text"])
        self.assertEqual(saved["message_id"], "msg-1")
        self.assertEqual(saved["body"].count("Not provided"), 5)

    def test_missing_or_already_contacted_lead_never_calls_bridge(self):
        service = self.setup_service()
        self.repository.get_by_id.return_value = None
        with self.assertRaisesRegex(ValueError, "不存在"):
            service.process_one("missing")
        self.repository.get_by_id.return_value = dict(self.lead, customer_added=True, customer_id=42)
        self.assertEqual(service.process_one("lead-1").status, "already_contacted")
        self.assertEqual(self.requests, [])
        self.repository.save_first_contact.assert_not_called()

    def test_unregistered_or_invalid_number_never_sends_or_saves(self):
        service = self.setup_service(registered=False)
        self.assertEqual(service.process_one("lead-1").status, "not_registered")
        self.assertEqual(len(self.requests), 1)
        self.lead["whatsapp_number"] = "13800000000"
        with self.assertRaises(ValueError):
            service.process_one("lead-1")
        self.assertEqual(len(self.requests), 1)
        self.repository.save_first_contact.assert_not_called()

    def test_uncertain_responses_and_timeout_never_save_or_retry(self):
        cases = [
            httpx.Response(200, json={"status": "submitted_unknown"}),
            httpx.Response(200, json={"status":"sent", "phone":"+8613900000000", "chat_jid":"123@lid", "message_id":"id"}),
            httpx.Response(500),
            httpx.Response(200, text="not JSON"),
        ]
        for response in cases:
            with self.subTest(response=response):
                service = self.setup_service(send_response=response)
                with self.assertRaises(SendOutcomeUnknown):
                    service.process_one("lead-1")
                self.repository.save_first_contact.assert_not_called()
                self.assertEqual(len(self.requests), 2)
        service = self.setup_service(send_error=httpx.ReadTimeout("test"))
        with self.assertRaises(SendOutcomeUnknown):
            service.process_one("lead-1")
        self.repository.save_first_contact.assert_not_called()
        self.assertEqual(len(self.requests), 2)

    def test_rejection_and_database_failure_are_distinct(self):
        service = self.setup_service(send_response=httpx.Response(503))
        with self.assertRaises(BridgeRequestError):
            service.process_one("lead-1")
        self.repository.save_first_contact.assert_not_called()
        service = self.setup_service()
        self.repository.save_first_contact.side_effect = RuntimeError("database unavailable")
        with self.assertRaisesRegex(ContactSaveError, "msg-1"):
            service.process_one("lead-1")
        self.assertEqual(len(self.requests), 2)


if __name__ == "__main__":
    unittest.main()
