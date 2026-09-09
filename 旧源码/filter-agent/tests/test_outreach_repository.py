import unittest

from filter_agent import AppSettings
from filter_agent.persistence.outreach_repository import MySQLOutreachRepository


class _Cursor:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount
        self.executions = []

    def execute(self, sql, params):
        self.executions.append((sql, params))

    def close(self):
        pass


class _Connection:
    def __init__(self, rowcount=1):
        self.cursor_instance = _Cursor(rowcount)
        self.committed = False
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        self.closed = True


class _LeasedJob:
    source_lead_id = "lead-1"
    idempotency_key = "key-1"
    lease_token = "lease-1"


class OutreachRepositoryTests(unittest.TestCase):
    def test_mark_sending_updates_only_the_claimed_job(self):
        connection = _Connection(rowcount=1)
        repository = MySQLOutreachRepository(
            AppSettings(), connection_factory=lambda: connection
        )

        self.assertTrue(repository.mark_sending(_LeasedJob()))
        sql, params = connection.cursor_instance.executions[0]
        self.assertIn("UPDATE lead_outreach", sql)
        self.assertIn("lease_token = %s", sql)
        self.assertEqual(params, ("lead-1", "key-1", "lease-1"))
        self.assertTrue(connection.committed)
        self.assertTrue(connection.closed)

    def test_mark_sending_returns_false_when_lease_was_lost(self):
        connection = _Connection(rowcount=0)
        repository = MySQLOutreachRepository(
            AppSettings(), connection_factory=lambda: connection
        )

        self.assertFalse(repository.mark_sending(_LeasedJob()))
        self.assertTrue(connection.committed)


if __name__ == "__main__":
    unittest.main()
