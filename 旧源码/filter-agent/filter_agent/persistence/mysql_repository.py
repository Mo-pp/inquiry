"""MySQL storage for customers and one-row-per-message records."""
from __future__ import annotations
from collections.abc import Callable
from typing import Any
import mysql.connector
from mysql.connector import Error as MySQLError
from ..classification.models import UnreadBatch
from ..config import AppSettings
from .time_parser import MessageTimeError, parse_message_time

class PersistenceError(RuntimeError):
    pass

class MySQLCustomerChatRepository:
    def __init__(self, settings: AppSettings, *, connection_factory: Callable[[], Any] | None = None) -> None:
        self._connect = connection_factory or (lambda: mysql.connector.connect(**settings.mysql_connect_kwargs()))

    def customer_exists(self, customer_phone: str) -> bool:
        connection = cursor = None
        try:
            connection = self._connect(); cursor = connection.cursor()
            cursor.execute("SELECT 1 FROM customers WHERE customer_phone = %s LIMIT 1", (customer_phone,))
            return cursor.fetchone() is not None
        except MySQLError as exc:
            raise PersistenceError("MySQL customer existence check failed") from exc
        finally:
            if cursor is not None: cursor.close()
            if connection is not None: connection.close()

    def append_relevant(self, batch: UnreadBatch) -> int:
        messages = batch.all_messages if batch.history_complete else batch.unread_messages
        if not messages: raise PersistenceError("No messages to persist")
        connection = cursor = None
        try:
            connection = self._connect(); connection.start_transaction(); cursor = connection.cursor()
            cursor.execute("SELECT first_message_at, last_message_at FROM customers WHERE customer_phone = %s FOR UPDATE", (batch.customer_phone,))
            customer = cursor.fetchone()
            parsed = [(message, parse_message_time(message.timestamp)) for message in messages]
            parsed.sort(key=lambda item: item[1])
            unique, seen = [], set()
            for message, message_at in parsed:
                if message.message_key not in seen:
                    unique.append((message, message_at)); seen.add(message.message_key)
            if not customer:
                first, last = min(x[1] for x in unique), max(x[1] for x in unique)
                cursor.execute("INSERT INTO customers (customer_phone, customer_name, first_message_at, last_message_at) VALUES (%s, %s, %s, %s)", (batch.customer_phone, batch.customer_name, first, last))
            appended = 0
            inserted_times = []
            for message, message_at in unique:
                cursor.execute("INSERT IGNORE INTO customer_messages (customer_phone, message_key, sender, direction, message_at, message_text) VALUES (%s, %s, %s, %s, %s, %s)", (batch.customer_phone, message.message_key, message.sender, message.direction, message_at, message.text))
                appended += cursor.rowcount
                if cursor.rowcount: inserted_times.append(message_at)
            if not customer and not inserted_times:
                connection.rollback()
                return 0
            if not customer:
                first, last = min(inserted_times), max(inserted_times)
                cursor.execute("UPDATE customers SET first_message_at = %s, last_message_at = %s WHERE customer_phone = %s", (first, last, batch.customer_phone))
            if customer and inserted_times:
                first = min(customer[0], *inserted_times); last = max(customer[1], *inserted_times)
                cursor.execute("UPDATE customers SET customer_name = %s, first_message_at = %s, last_message_at = %s WHERE customer_phone = %s", (batch.customer_name, first, last, batch.customer_phone))
            connection.commit(); return appended
        except (MySQLError, MessageTimeError, ValueError, TypeError) as exc:
            if connection is not None: connection.rollback()
            raise PersistenceError(str(exc) if isinstance(exc, MessageTimeError) else "MySQL append transaction failed") from exc
        finally:
            if cursor is not None: cursor.close()
            if connection is not None: connection.close()
