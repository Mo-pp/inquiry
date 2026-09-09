import unittest
from datetime import datetime

from filter_agent.services.inbound_ingestion import InboundIngestor, InboundWorker
from filter_agent.persistence.inbox_repository import EnqueueResult, InboxJob


class Queue:
    def __init__(self):
        self.envelopes = []
        self.job = None
        self.completed = []
        self.failed = []

    def enqueue(self, envelope):
        self.envelopes.append(envelope)
        return EnqueueResult(envelope.message_key, 1, len(self.envelopes) == 1, "pending")

    def claim_one(self):
        job, self.job = self.job, None
        return job

    def complete(self, job, *, result_status):
        self.completed.append((job, result_status))
        return True

    def fail(self, job, *, error, retry_at=None):
        self.failed.append((job, error, retry_at))
        return True


class Processor:
    def process(self, batch):
        return type("Result", (), {"status": "stored", "appended_count": 1})()


class InboundServiceTests(unittest.TestCase):
    def event(self):
        return {
            "message_id": "message-1",
            "chat_jid": "123456789012345@lid",
            "customer_phone": "+15555550100",
            "sender": "Test",
            "text": "Hello",
            "timestamp": "2026-09-03T01:02:03Z",
        }

    def test_ingestor_is_idempotent_at_queue_boundary(self):
        queue = Queue()
        ingestor = InboundIngestor(queue)
        first = ingestor.accept(self.event())
        second = ingestor.accept(self.event())
        self.assertEqual(first.status, "accepted")
        self.assertEqual(second.status, "duplicate")

    def test_dry_run_worker_does_not_claim_or_process(self):
        queue = Queue()
        queue.job = InboxJob(
            job_id=1,
            message_key="a" * 64,
            wa_message_id="m",
            chat_jid="123@lid",
            customer_phone="+15555550100",
            sender="Test",
            direction="in",
            message_at=datetime(2026, 9, 3, 1, 2, 3),
            message_text="Hello",
            raw_payload={},
            status="processing",
            attempts=1,
            lease_token="lease",
        )
        worker = InboundWorker(queue, Processor())
        result = worker.run_once(dry_run=True)
        self.assertEqual(result.status, "dry_run")
        self.assertEqual(queue.completed, [])
        self.assertIsNotNone(queue.job)

    def test_processing_failure_releases_job_for_retry(self):
        queue = Queue()
        queue.job = InboxJob(
            job_id=2,
            message_key="b" * 64,
            wa_message_id="m",
            chat_jid="123@lid",
            customer_phone="+15555550100",
            sender="Test",
            direction="in",
            message_at=datetime(2026, 9, 3, 1, 2, 3),
            message_text="Hello",
            raw_payload={},
            status="processing",
            attempts=1,
            lease_token="lease",
        )

        class FailingProcessor:
            def process(self, _batch):
                raise TimeoutError("model timeout")

        with self.assertRaises(TimeoutError):
            InboundWorker(queue, FailingProcessor(), retry_delays_seconds=(30,)).run_once(
                dry_run=False
            )
        self.assertEqual(len(queue.failed), 1)
        self.assertIsNotNone(queue.failed[0][2])


if __name__ == "__main__":
    unittest.main()
