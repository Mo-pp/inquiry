import unittest

from filter_agent.conversation import (
    clean_questions,
    infer_customer_level,
    parse_area_square_meters,
    positive_questions_for,
    question_codes_in_messages,
)


class ConversationWorkflowTests(unittest.TestCase):
    def test_parses_rectangle_area_and_selects_large_home_questions(self):
        lead = {
            "business_role_answer": "_personal_buyer",
            "target_country_answer": "Nigeria for personal use",
            "coverage_area_answer": "100 by 50 meter",
            "purchase_purpose_answer": "home use",
        }
        self.assertEqual(parse_area_square_meters("100 by 50 meter"), 5000)
        self.assertEqual(infer_customer_level(lead), "E")
        questions = positive_questions_for(lead)
        self.assertEqual(len(questions), 3)
        self.assertEqual(
            [question.code for question in questions],
            ["E_USAGE", "E_MULTI_AREA", "E_SIGNAL_SOURCE"],
        )

    def test_ambiguous_lead_gets_neutral_questions(self):
        questions = positive_questions_for({"business_role_answer": "buyer"})
        self.assertEqual(infer_customer_level({"business_role_answer": "buyer"}), "UNKNOWN")
        self.assertEqual(questions[0].code, "UNKNOWN_USAGE")

    def test_custom_questions_are_capped_and_exact_questions_are_tracked(self):
        self.assertEqual(clean_questions([" What is the site address? ", ""]), ["What is the site address?"])
        with self.assertRaises(ValueError):
            clean_questions(["1", "2", "3", "4"])
        messages = [
            {
                "direction": "out",
                "message_text": "Is there a usable mobile signal source within about 1 km of the site?",
            },
            {"direction": "in", "message_text": "Yes"},
        ]
        self.assertEqual(
            question_codes_in_messages(messages, level="E"), ("E_SIGNAL_SOURCE",)
        )


if __name__ == "__main__":
    unittest.main()
