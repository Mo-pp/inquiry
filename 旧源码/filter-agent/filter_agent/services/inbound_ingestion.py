"""Service boundary between WAJS events and the existing filter-agent.

The ingestion endpoint only persists and acknowledges an event.  Classification
and customer-table writes happen later in a worker, preserving the old
``ProcessUnreadBatch`` contract while adding a durable hand-off point.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from ..classification.models import UnreadBatch
from ..inbound import InboundMessageError, normalize_inbound_event
from ..persistence.inbox_repository import (
    EnqueueResult,
    InboxJob,
    MySQLInboxRepository,
)


class InboundQueue(Protocol):
    def enqueue(self, envelope) -> EnqueueResult: ...

    def claim_one(self) -> InboxJob | None: ...

    def complete(self, job: InboxJob, *, result_status: str) -> bool: ...

    def fail(self, job: InboxJob, *, error: str, retry_at=None) -> bool: ...

    def build_batch(self, job: InboxJob) -> UnreadBatch: ...


@dataclass(frozen=True, slots=True)
class IngestResult:
    status: str
    message_key: str | None = None
    job_id: int | None = None
    detail: str | None = None


class InboundIngestor:
    """Normalize one bridge event and enqueue it idempotently."""

    def __init__(self, queue: InboundQueue) -> None:
        self._queue = queue

    def accept(self, payload: Mapping[str, Any]) -> IngestResult:
        event_type = str(payload.get("event_type") or "message").strip().lower()
        if event_type not in {"message", "message_create"}:
            return IngestResult(status="ignored", detail=f"unsupported event type: {event_type}")
        try:
            envelope = normalize_inbound_event(payload)
        except InboundMessageError:
            raise
        if envelope is None:
            return IngestResult(status="ignored")
        result = self._queue.enqueue(envelope)
        return IngestResult(
            status="accepted" if result.inserted else "duplicate",
            message_key=result.message_key,
            job_id=result.job_id,
            detail=result.status,
        )


class InboxBatchProcessor(Protocol):
    def process(self, batch: UnreadBatch): ...


class InboundWorker:
    """Claim and hand off queued messages without owning a scheduler.

    The class is intentionally not started by FastAPI application assembly.
    A later deployment can run it from a dedicated process or Windows service;
    phase 3 tests call ``run_once(dry_run=True)`` with fakes only.
    """

    def __init__(
        self,
        queue: InboundQueue,
        processor: InboxBatchProcessor,
        *,
        retry_delays_seconds: tuple[int, ...] = (30, 120, 600),
    ) -> None:
        self._queue = queue
        self._processor = processor
        if any(delay <= 0 for delay in retry_delays_seconds):
            raise ValueError("retry delays must be positive")
        self._retry_delays_seconds = retry_delays_seconds

    def run_once(self, *, dry_run: bool = True) -> IngestResult | None:
        if dry_run:
            # Claiming a row changes its lease/attempt counters, so a true
            # dry-run must not even claim a production queue row.  Use the
            # explicit non-dry path only after the phase gate.
            return IngestResult(
                status="dry_run",
                detail="queue claim disabled in dry-run mode",
            )
        job = self._queue.claim_one()
        if job is None:
            return None
        if not job.customer_phone:
            if not self._queue.complete(job, result_status="manual_review"):
                raise RuntimeError("inbound job lease was lost before completion")
            return IngestResult(
                status="manual_review",
                message_key=job.message_key,
                job_id=job.job_id,
                detail="phone mapping is not available",
            )
        try:
            builder = getattr(self._queue, "build_batch", None)
            if callable(builder):
                batch = builder(job)
            else:
                batch = UnreadBatch(
                    customer_name=job.sender or job.customer_phone,
                    customer_phone=job.customer_phone,
                    context_messages=[],
                    unread_messages=[
                        {
                            "message_key": job.message_key,
                            "sender": job.sender,
                            "direction": "in",
                            "timestamp": job.message_at.isoformat(),
                            "text": job.message_text,
                        }
                    ],
                )
            result = self._processor.process(batch)
        except Exception as exc:
            attempt_index = max(job.attempts - 1, 0)
            retry_delay = (
                self._retry_delays_seconds[attempt_index]
                if attempt_index < len(self._retry_delays_seconds)
                else None
            )
            retry_at = (
                datetime.now(timezone.utc) + timedelta(seconds=retry_delay)
                if retry_delay is not None
                else None
            )
            self._queue.fail(job, error=str(exc), retry_at=retry_at)
            raise
        queue_status = "done" if result.status == "stored" else "filtered"
        if not self._queue.complete(job, result_status=queue_status):
            raise RuntimeError("inbound job lease was lost before completion")
        return IngestResult(
            status=queue_status,
            message_key=job.message_key,
            job_id=job.job_id,
            detail=f"appended_count={result.appended_count}",
        )


def build_default_ingestor(settings) -> InboundIngestor:
    """Construct the production queue adapter without opening a connection."""

    return InboundIngestor(MySQLInboxRepository(settings))
