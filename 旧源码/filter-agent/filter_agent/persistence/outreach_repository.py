"""Persistence adapter for the minimal proactive-outreach table.

No method in this module sends WhatsApp messages.  It only creates/claims a
lead job and records the result supplied by a separately gated sender.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any
import uuid

import mysql.connector
from mysql.connector import Error as MySQLError

from ..config import AppSettings
from ..services.lead_outreach import OutreachPlan, OutreachStatus


class OutreachPersistenceError(RuntimeError):
    """Raised when an outreach job cannot be persisted."""


@dataclass(frozen=True, slots=True)
class OutreachJob:
    source_lead_id: str
    idempotency_key: str
    status: OutreachStatus
    phone_e164: str | None
    chat_jid: str | None
    message_text_hash: str | None
    wa_message_id: str | None
    attempt_count: int
    last_error: str | None
    lease_token: str | None


class MySQLOutreachRepository:
    """Store one idempotent row per lead/template pair."""

    _ENSURE_SQL = """
        INSERT INTO lead_outreach (
            source_lead_id, idempotency_key, phone_e164, status,
            template_version, message_text_hash
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            idempotency_key = VALUES(idempotency_key),
            phone_e164 = COALESCE(lead_outreach.phone_e164, VALUES(phone_e164)),
            message_text_hash = COALESCE(
                lead_outreach.message_text_hash, VALUES(message_text_hash)
            ),
            status = CASE
                WHEN lead_outreach.status IN (
                    'sent', 'submitted_unknown', 'skipped_existing',
                    'opted_out', 'blocked'
                ) THEN lead_outreach.status
                WHEN VALUES(status) IN ('ready_to_send', 'checking_registration', 'sending')
                    THEN VALUES(status)
                ELSE lead_outreach.status
            END
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

    def ensure_plan(self, plan: OutreachPlan, *, status: OutreachStatus | None = None) -> None:
        connection = cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            cursor.execute(
                self._ENSURE_SQL,
                (
                    plan.source_lead_id,
                    plan.idempotency_key,
                    plan.phone_e164,
                    (status or plan.status).value,
                    plan.template_version,
                    plan.message_text_hash,
                ),
            )
            connection.commit()
        except MySQLError as exc:
            if connection is not None:
                connection.rollback()
            raise OutreachPersistenceError("failed to create outreach job") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def list_ready_jobs(self, *, limit: int = 100) -> list[OutreachJob]:
        """Read due jobs whose approval and opt-in timestamps are present."""

        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        connection = cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT source_lead_id, idempotency_key, status, phone_e164,
                       chat_jid, message_text_hash, wa_message_id, attempt_count,
                       last_error
                FROM lead_outreach
                WHERE status IN ('ready_to_send', 'retry_waiting')
                  AND opt_in_at IS NOT NULL
                  AND approved_at IS NOT NULL
                  AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                  AND (lease_until IS NULL OR lease_until < NOW())
                ORDER BY COALESCE(next_attempt_at, created_at), source_lead_id
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [
                OutreachJob(
                    source_lead_id=row["source_lead_id"],
                    idempotency_key=row["idempotency_key"],
                    status=OutreachStatus(row["status"]),
                    phone_e164=row.get("phone_e164"),
                    chat_jid=row.get("chat_jid"),
                    message_text_hash=row.get("message_text_hash"),
                    wa_message_id=row.get("wa_message_id"),
                    attempt_count=int(row.get("attempt_count") or 0),
                    last_error=row.get("last_error"),
                    lease_token=None,
                )
                for row in rows
            ]
        except (MySQLError, TypeError, ValueError) as exc:
            raise OutreachPersistenceError("failed to list ready outreach jobs") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def record_approval(
        self,
        *,
        source_lead_id: str,
        opted_in: bool,
        approved: bool,
        opt_in_source: str | None = None,
    ) -> bool:
        """Persist the two independent gates without sending anything."""

        status = (
            OutreachStatus.READY_TO_SEND.value
            if opted_in and approved
            else OutreachStatus.PENDING_APPROVAL.value
        )
        connection = cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE lead_outreach
                SET status = %s,
                    opt_in_at = CASE WHEN %s THEN COALESCE(opt_in_at, NOW()) ELSE NULL END,
                    opt_in_source = CASE WHEN %s THEN %s ELSE opt_in_source END,
                    approved_at = CASE WHEN %s THEN COALESCE(approved_at, NOW()) ELSE NULL END
                WHERE source_lead_id = %s
                  AND status NOT IN ('sent', 'submitted_unknown', 'skipped_existing', 'opted_out', 'blocked')
                """,
                (
                    status,
                    opted_in,
                    opted_in,
                    opt_in_source,
                    approved,
                    source_lead_id,
                ),
            )
            connection.commit()
            return cursor.rowcount == 1
        except MySQLError as exc:
            if connection is not None:
                connection.rollback()
            raise OutreachPersistenceError("failed to record outreach approval") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def claim_for_send(self, *, idempotency_key: str) -> OutreachJob | None:
        """Claim a ready/retryable job with a lease, never a sent job."""

        connection = cursor = None
        token = str(uuid.uuid4())
        try:
            connection = self._connect()
            connection.start_transaction()
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """
                SELECT source_lead_id, idempotency_key, status, phone_e164,
                       chat_jid, message_text_hash, wa_message_id, attempt_count,
                       last_error, lease_token
                FROM lead_outreach
                WHERE idempotency_key = %s
                  AND status IN ('ready_to_send', 'retry_waiting')
                  AND (next_attempt_at IS NULL OR next_attempt_at <= NOW())
                  AND (lease_until IS NULL OR lease_until < NOW())
                FOR UPDATE SKIP LOCKED
                """,
                (idempotency_key,),
            )
            row = cursor.fetchone()
            if not row:
                connection.commit()
                return None
            cursor.execute(
                """
                UPDATE lead_outreach
                SET status = 'checking_registration',
                    attempt_count = attempt_count + 1,
                    lease_token = %s,
                    lease_until = DATE_ADD(NOW(), INTERVAL 5 MINUTE),
                    last_attempt_at = NOW()
                WHERE idempotency_key = %s
                """,
                (token, idempotency_key),
            )
            connection.commit()
            return OutreachJob(
                source_lead_id=row["source_lead_id"],
                idempotency_key=row["idempotency_key"],
                status=OutreachStatus.CHECKING_REGISTRATION,
                phone_e164=row.get("phone_e164"),
                chat_jid=row.get("chat_jid"),
                message_text_hash=row.get("message_text_hash"),
                wa_message_id=row.get("wa_message_id"),
                attempt_count=int(row.get("attempt_count") or 0) + 1,
                last_error=row.get("last_error"),
                lease_token=token,
            )
        except MySQLError as exc:
            if connection is not None:
                connection.rollback()
            raise OutreachPersistenceError("failed to claim outreach job") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def mark_sending(self, job: OutreachJob) -> bool:
        """Advance a leased job only after registration returned a chat id."""

        return self._update_claimed(
            job,
            "SET status = 'sending'",
            (),
        )

    def _update_claimed(
        self,
        job: OutreachJob,
        set_clause: str,
        params: tuple[Any, ...],
    ) -> bool:
        """Update one leased outreach row without touching another attempt."""

        connection = cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            cursor.execute(
                f"""
                UPDATE lead_outreach
                {set_clause}
                WHERE source_lead_id = %s
                  AND idempotency_key = %s
                  AND lease_token = %s
                """,
                (*params, job.source_lead_id, job.idempotency_key, job.lease_token),
            )
            connection.commit()
            return cursor.rowcount == 1
        except MySQLError as exc:
            if connection is not None:
                connection.rollback()
            raise OutreachPersistenceError("failed to update outreach job") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def recover_expired_sends(self) -> int:
        """Move abandoned ``sending`` leases to manual reconciliation.

        A crashed worker cannot tell whether WhatsApp accepted its request;
        automatically retrying could duplicate the first message.  Marking the
        row ``submitted_unknown`` preserves that uncertainty explicitly.
        """

        connection = cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE lead_outreach
                SET status = 'submitted_unknown',
                    last_error = 'send worker lease expired; reconcile before retry',
                    lease_until = NULL,
                    lease_token = NULL
                WHERE status IN ('checking_registration', 'sending')
                  AND lease_until < NOW()
                """
            )
            connection.commit()
            return cursor.rowcount
        except MySQLError as exc:
            if connection is not None:
                connection.rollback()
            raise OutreachPersistenceError("failed to recover expired outreach leases") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def record_result(
        self,
        job: OutreachJob,
        *,
        status: OutreachStatus,
        message_id: str | None = None,
        chat_jid: str | None = None,
        error: str | None = None,
        retry_at: datetime | None = None,
        event: Mapping[str, Any] | None = None,
    ) -> bool:
        """Record a gated sender result while retaining a compact JSON history."""

        connection = cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            cursor.execute(
                """
                UPDATE lead_outreach
                SET status = %s,
                    wa_message_id = COALESCE(%s, wa_message_id),
                    chat_jid = COALESCE(%s, chat_jid),
                    last_error = %s,
                    next_attempt_at = %s,
                    contacted_at = CASE WHEN %s = 'sent' THEN NOW() ELSE contacted_at END,
                    lease_until = NULL,
                    lease_token = NULL,
                    attempt_history = JSON_ARRAY_APPEND(
                        COALESCE(attempt_history, JSON_ARRAY()), '$', CAST(%s AS JSON)
                    )
                WHERE source_lead_id = %s AND idempotency_key = %s
                  AND lease_token = %s
                """,
                (
                    status.value,
                    message_id,
                    chat_jid,
                    error[:2000] if error else None,
                    retry_at,
                    status.value,
                    json.dumps(
                        {
                            "status": status.value,
                            "message_id": message_id,
                            "error": error,
                            "at": datetime.utcnow().isoformat(),
                            **(dict(event) if event else {}),
                        },
                        ensure_ascii=False,
                    ),
                    job.source_lead_id,
                    job.idempotency_key,
                    job.lease_token,
                ),
            )
            connection.commit()
            return cursor.rowcount == 1
        except MySQLError as exc:
            if connection is not None:
                connection.rollback()
            raise OutreachPersistenceError("failed to record outreach result") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()

    def record_outbound_message(
        self,
        *,
        phone: str,
        customer_name: str | None,
        text: str,
        message_id: str,
        chat_jid: str | None = None,
    ) -> bool:
        """Persist a successful proactive message in the normal chat history.

        The WAJS message id is the idempotency source.  A message without a
        reliable id is deliberately not accepted here; the outreach row keeps
        that operation as ``submitted_unknown`` until it is reconciled.
        """

        if not str(phone).startswith("+") or not str(message_id).strip():
            raise ValueError("an E.164 phone and message_id are required")
        if not str(text).strip():
            raise ValueError("outbound message text is required")
        message_key = hashlib.sha256(
            f"agent-outreach:\x1f{message_id}".encode("utf-8")
        ).hexdigest()
        sent_at = datetime.utcnow()
        name = str(customer_name or phone).strip() or phone
        connection = cursor = None
        try:
            connection = self._connect()
            connection.start_transaction()
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO customers (
                    customer_phone, customer_name, first_message_at, last_message_at
                ) VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    last_message_at = GREATEST(last_message_at, VALUES(last_message_at))
                """,
                (phone, name, sent_at, sent_at),
            )
            cursor.execute(
                """
                INSERT IGNORE INTO customer_messages (
                    customer_phone, message_key, sender, direction,
                    message_at, message_text
                ) VALUES (%s, %s, %s, 'out', %s, %s)
                """,
                (phone, message_key, "luna", sent_at, text),
            )
            inserted = cursor.rowcount == 1
            connection.commit()
            return inserted
        except (MySQLError, TypeError, ValueError) as exc:
            if connection is not None:
                connection.rollback()
            if isinstance(exc, ValueError):
                raise
            raise OutreachPersistenceError("failed to record outbound message") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
