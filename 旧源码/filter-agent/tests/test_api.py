import unittest

from fastapi.testclient import TestClient

from filter_agent.api.app import create_app
from filter_agent.persistence import PersistenceError
from filter_agent import AppSettings
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

    def append_relevant(self, _batch):
        if self.error:
            raise self.error
        return self.count

    def customer_exists(self, _phone):
        if self.error:
            raise self.error
        return True


class _Inbound:
    def __init__(self, status="accepted"):
        self.status = status
        self.calls = []

    def accept(self, payload):
        self.calls.append(payload)
        return type(
            "Result",
            (),
            {
                "status": self.status,
                "message_key": "a" * 64,
                "job_id": 1,
                "detail": "pending",
            },
        )()


class ApiTests(unittest.TestCase):
    def make_client(self, classifier=None, repository=None, inbound=None, settings=None):
        return TestClient(
            create_app(
                classifier=classifier or _Classifier(),
                repository=repository or _Repository(),
                inbound_ingestor=inbound,
                settings=settings,
            )
        )

    def test_health(self):
        response = self.make_client().get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_customer_exists(self):
        response = self.make_client().get("/api/v1/customers/%2B8613800000000/exists")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"exists": True})

    def test_customer_exists_database_error_is_503(self):
        response = self.make_client(repository=_Repository(error=PersistenceError("down"))).get(
            "/api/v1/customers/%2B8613800000000/exists"
        )
        self.assertEqual(response.status_code, 503)

    def test_stored_and_filtered_responses(self):
        payload = make_batch().model_dump()
        stored = self.make_client(_Classifier(True), _Repository(1)).post(
            "/api/v1/unread-batches", json=payload
        )
        filtered = self.make_client(_Classifier(False), _Repository()).post(
            "/api/v1/unread-batches", json=payload
        )
        self.assertEqual(
            stored.json(),
            {"status": "stored", "is_relevant": True, "appended_count": 1},
        )
        self.assertEqual(
            filtered.json(),
            {"status": "filtered", "is_relevant": False, "appended_count": 0},
        )

    def test_validation_error_is_422(self):
        payload = make_batch().model_dump()
        payload["customer_phone"] = "not-a-phone"
        response = self.make_client().post("/api/v1/unread-batches", json=payload)
        self.assertEqual(response.status_code, 422)

    def test_model_error_is_502(self):
        response = self.make_client(_Classifier(error=TimeoutError())).post(
            "/api/v1/unread-batches", json=make_batch().model_dump()
        )
        self.assertEqual(response.status_code, 502)

    def test_mysql_error_is_503(self):
        response = self.make_client(
            _Classifier(True), _Repository(error=PersistenceError("down"))
        ).post("/api/v1/unread-batches", json=make_batch().model_dump())
        self.assertEqual(response.status_code, 503)

    def test_inbound_endpoint_is_disabled_by_default(self):
        response = self.make_client(inbound=_Inbound()).post(
            "/api/v1/whatsapp/inbound",
            json={"chat_jid": "123456789012345@lid", "timestamp": 1, "text": "Hello"},
        )
        self.assertEqual(response.status_code, 503)

    def test_inbound_endpoint_enqueues_when_explicitly_enabled(self):
        inbound = _Inbound()
        settings = AppSettings(whatsapp_inbound_enabled=True)
        response = self.make_client(inbound=inbound, settings=settings).post(
            "/api/v1/whatsapp/inbound",
            json={
                "chat_jid": "123456789012345@lid",
                "customer_phone": "+15555550100",
                "timestamp": 1,
                "text": "Hello",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(inbound.calls[0]["text"], "Hello")


if __name__ == "__main__":
    unittest.main()
