import unittest

from filter_agent.persistence import PersistenceError
from filter_agent.services import ModelUnavailableError, ProcessUnreadBatch
from tests.helpers import make_batch


class _Classifier:
    def __init__(self, result=True, error=None):
        self.result = result
        self.error = error

    def classify(self, _batch):
        if self.error:
            raise self.error
        return self.result


class _Repository:
    def __init__(self, count=1, error=None):
        self.count = count
        self.error = error
        self.calls = 0

    def append_relevant(self, _batch):
        self.calls += 1
        if self.error:
            raise self.error
        return self.count

    def customer_exists(self, _phone):
        return True


class ProcessUnreadBatchTests(unittest.TestCase):
    def test_related_batch_is_stored(self):
        repository = _Repository(count=1)
        result = ProcessUnreadBatch(_Classifier(True), repository).process(make_batch())
        self.assertEqual(
            (result.status, result.is_relevant, result.appended_count),
            ("stored", True, 1),
        )
        self.assertEqual(repository.calls, 1)

    def test_unrelated_batch_does_not_touch_mysql(self):
        repository = _Repository()
        result = ProcessUnreadBatch(_Classifier(False), repository).process(make_batch())
        self.assertEqual(
            (result.status, result.is_relevant, result.appended_count),
            ("filtered", False, 0),
        )
        self.assertEqual(repository.calls, 0)

    def test_model_error_is_explicit(self):
        with self.assertRaises(ModelUnavailableError):
            ProcessUnreadBatch(
                _Classifier(error=TimeoutError()), _Repository()
            ).process(make_batch())

    def test_repository_error_is_not_hidden(self):
        with self.assertRaises(PersistenceError):
            ProcessUnreadBatch(
                _Classifier(True),
                _Repository(error=PersistenceError("down")),
            ).process(make_batch())


if __name__ == "__main__":
    unittest.main()
