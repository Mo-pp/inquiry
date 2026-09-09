import unittest

import httpx

from filter_agent.services.whatsapp_bridge import HttpWhatsAppBridge, WhatsAppBridgeError


class WhatsAppBridgeAdapterTests(unittest.TestCase):
    def test_adapter_maps_check_and_send_requests(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path.endswith("/messages/check"):
                return httpx.Response(
                    200,
                    json={
                        "status": "registered",
                        "phone": "+15555550100",
                        "chat_jid": "15555550100@c.us",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "status": "sent",
                    "message_id": "mock-message-id",
                    "delivery_status": "unknown",
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        bridge = HttpWhatsAppBridge(client=client)
        self.assertEqual(bridge.check_number("+15555550100")["status"], "registered")
        self.assertEqual(
            bridge.send_text("+15555550100", "hello")["message_id"],
            "mock-message-id",
        )
        self.assertEqual([request.url.path for request in requests], [
            "/messages/check",
            "/messages/send",
        ])
        client.close()

    def test_adapter_exposes_bridge_error_kind_without_retrying_it(self):
        def handler(_request):
            return httpx.Response(503, json={"detail": "WhatsApp is not ready"})

        client = httpx.Client(transport=httpx.MockTransport(handler))
        bridge = HttpWhatsAppBridge(client=client)
        with self.assertRaises(WhatsAppBridgeError) as caught:
            bridge.send_text("+15555550100", "hello")
        self.assertEqual(caught.exception.error_kind, "not_ready")
        client.close()

    def test_adapter_includes_transport_details_for_invalid_json(self):
        def handler(_request):
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html>bridge is not the expected service</html>",
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        bridge = HttpWhatsAppBridge(client=client)
        with self.assertRaises(WhatsAppBridgeError) as caught:
            bridge.check_number("+15555550100")
        self.assertEqual(caught.exception.status_code, 200)
        self.assertIn("content-type text/html", str(caught.exception))
        self.assertIn("bridge is not the expected service", str(caught.exception))
        client.close()


if __name__ == "__main__":
    unittest.main()
