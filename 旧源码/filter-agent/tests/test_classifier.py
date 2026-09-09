import unittest
import json

from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from filter_agent import (
    AppSettings,
    RelevanceClassifier,
    RelevanceDecision,
    UnreadBatch,
)
from filter_agent.classification.llm import build_chat_model
from filter_agent.classification.serialization import serialize_unread_batch
from tests.helpers import make_batch


class _FakeLLM:
    def with_structured_output(self, schema, *, method):
        self.method = method
        return RunnableLambda(lambda _prompt: schema(is_relevant=True))


class RelevanceClassifierTests(unittest.TestCase):
    def test_serialization_keeps_context_and_unread_separate(self):
        serialized = serialize_unread_batch(make_batch())
        self.assertIn('"context_messages"', serialized)
        self.assertIn('"unread_messages"', serialized)
        self.assertIn("地下室没有手机信号", serialized)

    def test_complete_history_is_required_but_not_sent_to_classifier(self):
        payload = make_batch().model_dump()
        payload["history_complete"] = True
        payload["all_messages"] = [payload["context_messages"][0], payload["unread_messages"][0]]
        batch = UnreadBatch.model_validate(payload)
        serialized = json.loads(serialize_unread_batch(batch))
        self.assertNotIn("all_messages", serialized)
        payload["all_messages"] = []
        with self.assertRaises(ValidationError):
            UnreadBatch.model_validate(payload)

    def test_phone_requires_international_format(self):
        payload = make_batch().model_dump()
        payload["customer_phone"] = "13800000000"
        with self.assertRaises(ValidationError):
            UnreadBatch.model_validate(payload)

    def test_context_is_limited_to_five_messages(self):
        payload = make_batch().model_dump()
        payload["context_messages"] *= 6
        with self.assertRaises(ValidationError):
            UnreadBatch.model_validate(payload)

    def test_unread_messages_must_be_incoming(self):
        payload = make_batch().model_dump()
        payload["unread_messages"][0]["direction"] = "out"
        with self.assertRaises(ValidationError):
            UnreadBatch.model_validate(payload)

    def test_classifier_uses_strict_structured_output(self):
        fake_llm = _FakeLLM()
        classifier = RelevanceClassifier(llm=fake_llm)
        self.assertTrue(classifier.classify(make_batch()))
        self.assertEqual(fake_llm.method, "function_calling")

        with self.assertRaises(ValidationError):
            RelevanceDecision.model_validate({"is_relevant": 1})

    def test_deepseek_thinking_mode_is_disabled(self):
        model = build_chat_model(AppSettings(api_key="test-key"))
        self.assertEqual(model.extra_body, {"thinking": {"type": "disabled"}})


if __name__ == "__main__":
    unittest.main()
