import unittest
from datetime import datetime

from filter_agent.inbound import InboundMessageError, normalize_inbound_event, normalize_phone


class InboundNormalizationTests(unittest.TestCase):
    def test_normalizes_lid_event_with_explicit_phone(self):
        event = normalize_inbound_event(
            {
                "message_id": "true_123@c.us_ABC",
                "message_key": "A" * 64,
                "chat_jid": "218086426841304@lid",
                "customer_phone": "+1 (555) 555-0100",
                "sender": "Prospect",
                "text": "Hello",
                "timestamp": "2026-09-03T01:02:03Z",
            }
        )
        assert event is not None
        self.assertEqual(event.message_key, "a" * 64)
        self.assertEqual(event.customer_phone, "+15555550100")
        self.assertEqual(event.chat_jid, "218086426841304@lid")
        self.assertEqual(event.message_at, datetime(2026, 9, 3, 1, 2, 3))
        self.assertEqual(event.as_chat_message().direction, "in")

    def test_ignores_groups_and_self_authored_events(self):
        self.assertIsNone(
            normalize_inbound_event(
                {"chat_jid": "123@g.us", "timestamp": 1, "text": "hello"}
            )
        )
        self.assertIsNone(
            normalize_inbound_event(
                {
                    "chat_jid": "123@c.us",
                    "timestamp": 1,
                    "text": "hello",
                    "from_me": True,
                }
            )
        )

    def test_lid_without_phone_is_persistable_but_not_batchable(self):
        event = normalize_inbound_event(
            {"chat_jid": "123456789012345@lid", "timestamp": 1, "text": "hello"}
        )
        assert event is not None
        self.assertIsNone(event.customer_phone)
        with self.assertRaises(InboundMessageError):
            event.as_unread_batch()

    def test_phone_normalization_never_guesses_a_local_number(self):
        self.assertEqual(normalize_phone("00 1 555 555 0100"), "+15555550100")
        self.assertEqual(normalize_phone("+1 (555) 555-0100"), "+15555550100")
        self.assertIsNone(normalize_phone("13800000000"))
        self.assertEqual(
            normalize_phone("15555550100", allow_bare=True), "+15555550100"
        )


if __name__ == "__main__":
    unittest.main()
