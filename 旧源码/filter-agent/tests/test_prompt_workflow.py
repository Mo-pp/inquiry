import unittest

from scripts.process_unread_batch import build_prompt


class ReplyPromptWorkflowTests(unittest.TestCase):
    def test_lead_context_drives_follow_up_prompt_without_exposing_raw_lead_data(self):
        state = {
            "score": 50,
            "level_code": "E",
            "score_updated_at": None,
            "_lead_context": {
                "source_lead_id": "test-lead-1",
                "full_name": "Test Buyer",
                "business_role_answer": "_personal_buyer",
                "target_country_answer": "Nigeria for personal use",
                "coverage_area_answer": "100 by 50 meter",
                "purchase_purpose_answer": "home use",
                "frequency_budget_quantity_answer": "2G 3G 4G 5G",
            },
        }
        history = [
            {
                "direction": "out",
                "message_text": "Is there a usable mobile signal source within about 1 km of the site?",
            },
            {"direction": "in", "message_text": "Yes, there is signal outside."},
        ]
        prompt = build_prompt(
            "+15555550100",
            [{"message_text": "Yes"}],
            history,
            state,
            first_session=False,
        )
        self.assertIn("Nigeria for personal use", prompt)
        self.assertIn("100 by 50 meter", prompt)
        self.assertIn("多轮追问流程", prompt)
        self.assertIn("E_SIGNAL_SOURCE", prompt)
        self.assertIn("已由模板精确发送过的正向问题代码", prompt)


if __name__ == "__main__":
    unittest.main()
