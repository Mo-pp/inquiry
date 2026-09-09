"""Fail-closed phone checks for the proactive outreach gate."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import mysql.connector
from mysql.connector import Error as MySQLError

from ..config import AppSettings
from ..inbound import normalize_phone


class PhoneSafetyError(RuntimeError):
    """Raised when the production phone safety set cannot be checked."""


class MySQLKnownPhoneChecker:
    """Return whether an E.164 phone exists in either customer table.

    A database error is raised instead of returning ``False``.  The outreach
    executor checks this before registration lookup or sending, so an unknown
    safety result cannot accidentally allow a production number through.
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

    def __call__(
        self,
        phone: str,
        *,
        exclude_source_lead_id: str | None = None,
    ) -> bool:
        normalized = normalize_phone(phone)
        if normalized != phone:
            raise ValueError("phone safety checks require an E.164 phone")

        connection = cursor = None
        try:
            connection = self._connect()
            cursor = connection.cursor()
            digits = normalized[1:]
            cursor.execute(
                """
                SELECT 1
                FROM (
                    SELECT customer_phone AS phone
                    FROM customers
                    WHERE customer_phone IN (%s, %s)
                    UNION ALL
                    SELECT whatsapp_number AS phone
                    FROM customer_leads
                    WHERE whatsapp_number IN (%s, %s)
                      AND (%s IS NULL OR source_lead_id <> %s)
                ) AS known_phones
                LIMIT 1
                """,
                (
                    normalized,
                    digits,
                    normalized,
                    digits,
                    exclude_source_lead_id,
                    exclude_source_lead_id,
                ),
            )
            return cursor.fetchone() is not None
        except MySQLError as exc:
            raise PhoneSafetyError("MySQL phone safety check failed") from exc
        finally:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
