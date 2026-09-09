import unittest
from datetime import datetime
from mysql.connector import Error as MySQLError
from filter_agent import AppSettings
from filter_agent.persistence import MySQLCustomerChatRepository, PersistenceError
from filter_agent.persistence.time_parser import parse_message_time
from tests.helpers import make_batch
from filter_agent import UnreadBatch

class _Cursor:
    def __init__(self, row=None, rowcounts=None, fail_at=None):
        self.row, self.rowcounts, self.fail_at = row, list(rowcounts or []), fail_at
        self.executions, self.rowcount = [], 0
    def execute(self, sql, params):
        self.executions.append((sql, params))
        if self.fail_at == len(self.executions): raise MySQLError("down")
        self.rowcount = self.rowcounts.pop(0) if self.rowcounts else 1
    def fetchone(self): return self.row
    def close(self): pass

class _Connection:
    def __init__(self, row=None, rowcounts=None, fail_at=None):
        self.cursor_instance = _Cursor(row, rowcounts, fail_at)
        self.committed = self.rolled_back = self.closed = False
    def start_transaction(self): pass
    def cursor(self): return self.cursor_instance
    def commit(self): self.committed = True
    def rollback(self): self.rolled_back = True
    def close(self): self.closed = True

class RepositoryTests(unittest.TestCase):
    def test_service_iso_timestamp_is_accepted(self):
        self.assertEqual(
            parse_message_time("2026-09-03T01:02:03Z"),
            datetime(2026, 9, 3, 1, 2, 3),
        )

    def repository(self, connection):
        return MySQLCustomerChatRepository(AppSettings(), connection_factory=lambda: connection)
    def test_new_customer_inserts_customer_before_message(self):
        connection = _Connection(row=None, rowcounts=[1, 1, 1])
        self.assertEqual(self.repository(connection).append_relevant(make_batch()), 1)
        sql = [item[0] for item in connection.cursor_instance.executions]
        self.assertIn("INSERT INTO customers", sql[1])
        self.assertIn("INSERT IGNORE INTO customer_messages", sql[2])
        self.assertTrue(connection.committed)
    def test_existing_duplicate_returns_zero_without_time_update(self):
        existing = (datetime(2026, 8, 29, 10), datetime(2026, 8, 29, 10, 1))
        connection = _Connection(row=existing, rowcounts=[1, 0])
        self.assertEqual(self.repository(connection).append_relevant(make_batch()), 0)
        self.assertEqual(len(connection.cursor_instance.executions), 2)
    def test_complete_history_inserts_both_directions(self):
        payload = make_batch().model_dump()
        payload["history_complete"] = True
        payload["all_messages"] = [payload["context_messages"][0], payload["unread_messages"][0]]
        connection = _Connection(row=None, rowcounts=[1, 1, 1, 1, 1])
        self.assertEqual(self.repository(connection).append_relevant(UnreadBatch.model_validate(payload)), 2)
        inserts = [params for sql, params in connection.cursor_instance.executions if "INSERT IGNORE INTO customer_messages" in sql]
        self.assertEqual([params[3] for params in inserts], ["out", "in"])
    def test_mysql_error_rolls_back(self):
        connection = _Connection(fail_at=2)
        with self.assertRaises(PersistenceError): self.repository(connection).append_relevant(make_batch())
        self.assertTrue(connection.rolled_back and connection.closed)

if __name__ == "__main__": unittest.main()
