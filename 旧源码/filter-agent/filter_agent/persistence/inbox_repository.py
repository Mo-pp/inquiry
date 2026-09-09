"""Durable MySQL queue for WAJS inbound events.

The repository is intentionally independent from the classifier.  A bridge
event is committed to this queue first; a worker can then claim and process it
without losing it when the model, API, or process restarts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import json
import uuid
from typing import Any

import mysql.connector
from mysql.connector import Error as MySQLError

from ..config import AppSettings
from ..inbound import InboundEnvelope
from ..classification.models import ChatMessage, UnreadBatch


class InboxPersistenceError(RuntimeError):
    """Raised when the durable inbox cannot be read or written."""


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    message_key: str
    job_id: int | None
    inserted: bool
    status: str | None


@dataclass(frozen=True, slots=True)
class InboxJob:
    job_id: int
    message_key: str
    wa_message_id: str | None
    chat_jid: str
    customer_phone: str | None
    sender: str
    direction: str
    message_at: datetime
    message_text: str
    raw_payload: dict[str, Any]
    status: str
    attempts: int
    lease_token: str | None


class MySQLInboxRepository:
    """Store, claim, and complete inbound jobs with MySQL transactions."""

    _INSERT_SQL = """
        INSERT INTO wa_inbox_jobs (
            message_key, wa_message_id, chat_jid, customer_phone, sender,
            direction, message_at, message_text, raw_payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE message_key = VALUES(message_key)
    """

    def __init__(
        self,
        settings: AppSettings,
        *,
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._connect = connection_factory or (
            lambda: mysql.connector.connect(**settings.mysql_connect_kwargs())
        )
        self.lease_seconds = settings.whatsapp_inbox_lease_seconds

    def enqueue(self, envelope: InboundEnvelope) -> EnqueueResult:
        connection = cursor = None
        try:
            connection = self._connect()
            connection.start_transaction()
            cursor = connection.cursor()
            cursor.execute(
                self._INSERT_SQL,
                (
                    envelope.message_key,
                    envelope.wa_message_id,
                    envelope.chat_jid,
                    envelope.customer_phone,
                    envelope.sender,
                    envelope.direction,
                    envelope.message_at,
                    envelope.text,
                    json.dumps(envelope.raw_payload, ensure_ascii=False),
                ),
            )
            inserted = cursor.rowcount > 0
            cursor.execute(
                """
                SELECT id, status
                FROM wa_inbox_jobs
                WHERE message_key = %s
                   OR (wa_message_id = %s AND %s IS NOT NULL)
                ORDER BY id
                LIMIT 1
                """,
                (
                    envelope.message_key,
                    envelope.wa_message_id,
                    envelope.wa_message_id,
                ),
            )
            row = cursor.fetchone()
            connection.commit()
            return EnqueueResult(
                message_key=envelope.message_key,
                job_id=int(row[0]) if row else None,
                inserted=inserted,
                status=str(row[1]) if row else None,
            )
        except (MySQLError, TypeError, ValueError) as exc:
            if connection is not None:
                connection.rollback()
            raise InboxPersistenceError("failed to enqueue inbound message") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def claim_one(self) -> InboxJob | None:
        """Claim one pending/expired job using a lease token.

        MySQL 8/InnoDB supports ``SKIP LOCKED`` so multiple workers do not
        process the same message concurrently.  The migration deliberately
        uses ordinary indexed columns, keeping this query portable to MySQL
        8.4 without relying on a vendor queue product.
        """

        connection = cursor = None
        token = str(uuid.uuid4())
        try:
            connection = self._connect()
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT id, message_key, wa_message_id, chat_jid, customer_phone,
                       sender, direction, message_at, message_text, raw_payload,
                       status, attempts, lease_token
                FROM wa_inbox_jobs
                WHERE (
                        status = 'pending'
                        OR (status = 'processing' AND lease_until < NOW())
                      )
                  AND available_at <= NOW()
                  AND (lease_until IS NULL OR lease_until < NOW())
                ORDER BY id
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """
            )
            row = cursor.fetchone()
            if not row:
                connection.commit()
                return None
            cursor.execute(
                """
                UPDATE wa_inbox_jobs
                SET status = 'processing',
                    attempts = attempts + 1,
                    lease_token = %s,
                    lease_until = DATE_ADD(NOW(), INTERVAL %s SECOND)
                WHERE id = %s
                """,
                (token, self.lease_seconds, row["id"]),
            )
            connection.commit()
            raw_payload = row["raw_payload"]
            if isinstance(raw_payload, str):
                raw_payload = json.loads(raw_payload)
            return InboxJob(
                job_id=int(row["id"]),
                message_key=row["message_key"],
                wa_message_id=row.get("wa_message_id"),
                chat_jid=row["chat_jid"],
                customer_phone=row.get("customer_phone"),
                sender=row["sender"],
                direction=row["direction"],
                message_at=row["message_at"],
                message_text=row["message_text"],
                raw_payload=raw_payload if isinstance(raw_payload, dict) else {},
                status="processing",
                attempts=int(row["attempts"]) + 1,
                lease_token=token,
            )
        except (MySQLError, TypeError, ValueError, json.JSONDecodeError) as exc:
            if connection is not None:
                connection.rollback()
            raise InboxPersistenceError("failed to claim inbound message") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def build_batch(self, job: InboxJob) -> UnreadBatch:
        """Build the legacy classifier payload with up to five prior messages."""

        if not job.customer_phone:
            raise InboxPersistenceError("cannot build a batch without a phone mapping")
        connection = cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT message_key, sender, direction, message_at, message_text
                FROM customer_messages
                WHERE customer_phone = %s
                ORDER BY id DESC
                LIMIT 5
                """,
                (job.customer_phone,),
            )
            rows = list(reversed(cursor.fetchall()))
            context = [
                ChatMessage(
                    message_key=row["message_key"],
                    sender=row["sender"],
                    direction=row["direction"],
                    timestamp=row["message_at"].isoformat(),
                    text=row["message_text"],
                )
                for row in rows
                if row["message_key"] != job.message_key
            ]
            return UnreadBatch(
                customer_name=job.sender or job.customer_phone,
                customer_phone=job.customer_phone,
                context_messages=context[-5:],
                unread_messages=[
                    ChatMessage(
                        message_key=job.message_key,
                        sender=job.sender,
                        direction="in",
                        timestamp=job.message_at.isoformat(),
                        text=job.message_text,
                    )
                ],
            )
        except (MySQLError, TypeError, ValueError) as exc:
            raise InboxPersistenceError("failed to build classifier batch") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def complete(self, job: InboxJob, *, result_status: str) -> bool:
        return self._update_claimed(
            job,
            """
            SET status = %s, processed_at = NOW(), lease_until = NULL,
                lease_token = NULL, last_error = NULL
            """,
            (result_status,),
        )

    def fail(
        self,
        job: InboxJob,
        *,
        error: str,
        retry_at: datetime | None = None,
    ) -> bool:
        """Release a job for retry or move it to the failed state."""

        if retry_at is None:
            sql = """
                SET status = 'failed', processed_at = NULL,
                    lease_until = NULL, lease_token = NULL, last_error = %s
            """
            params = (error[:2000],)
        else:
            sql = """
                SET status = 'pending', available_at = %s, processed_at = NULL,
                    lease_until = NULL, lease_token = NULL, last_error = %s
            """
            params = (retry_at, error[:2000])
        return self._update_claimed(job, sql, params)

    def _update_claimed(
        self,
        job: InboxJob,
        set_clause: str,
        params: tuple[Any, ...],
    ) -> bool:
        connection = cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            cursor.execute(
                f"UPDATE wa_inbox_jobs {set_clause} WHERE id = %s AND lease_token = %s",
                (*params, job.job_id, job.lease_token),
            )
            connection.commit()
            return cursor.rowcount == 1
        except MySQLError as exc:
            if connection is not None:
                connection.rollback()
            raise InboxPersistenceError("failed to update inbound job") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
