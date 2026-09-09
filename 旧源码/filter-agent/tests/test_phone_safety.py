import unittest

from filter_agent.config import AppSettings
from filter_agent.persistence.phone_safety import MySQLKnownPhoneChecker


class _Cursor:
    def __init__(self, row=None):
        self.row = row
        self.calls = []
        self.closed = False

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


class PhoneSafetyTests(unittest.TestCase):
    def test_checker_queries_both_safety_tables_with_normalized_phone(self):
        cursor = _Cursor(row=(1,))
        connection = _Connection(cursor)
        checker = MySQLKnownPhoneChecker(
            AppSettings(), connection_factory=lambda: connection
        )

        self.assertTrue(checker("+15555550100"))
        self.assertEqual(
            cursor.calls[0][1],
            ("+15555550100", "15555550100", "+15555550100", "15555550100", None, None),
        )
        self.assertTrue(cursor.closed)
        self.assertTrue(connection.closed)

    def test_checker_returns_false_when_safety_set_is_empty(self):
        cursor = _Cursor(row=None)
        connection = _Connection(cursor)
        checker = MySQLKnownPhoneChecker(
            AppSettings(), connection_factory=lambda: connection
        )

        self.assertFalse(checker("+15555550100"))

    def test_checker_rejects_non_e164_input(self):
        checker = MySQLKnownPhoneChecker(AppSettings(), connection_factory=lambda: None)
        with self.assertRaises(ValueError):
            checker("55550100")


if __name__ == "__main__":
    unittest.main()
