import unittest

from filter_agent.scoring import (
    ScoreParseError,
    parse_score_result,
    render_score_message,
)


class ScoringTests(unittest.TestCase):
    def test_parse_tagged_score_result(self):
        decision = parse_score_result(
            "done\n<SCORE_RESULT>{"
            '\"score\":85,\"score_delta\":85,\"level_code\":\"A\",'
            '\"matched_points\":[{\"code\":\"A_BASE\",\"name\":\"基础分\",\"score\":50}],'
            '\"user_evidence\":[{\"rule_code\":\"A_BASE\",\"message_key\":\"msg-1\",\"quote\":\"商业办公楼\"}],'
            '\"information_sufficient\":true}</SCORE_RESULT>'
        )
        self.assertEqual((decision.score, decision.score_delta, decision.level_code), (85, 85, "A"))
        self.assertIn("基础分（+50分）", render_score_message(decision, language="zh"))
        self.assertIn("用户：商业办公楼", render_score_message(decision, language="zh"))

    def test_unknown_score_can_have_no_evidence(self):
        decision = parse_score_result(
            '<SCORE_RESULT>{"score":0,"score_delta":0,"level_code":"UNKNOWN",'
            '"matched_points":[],"user_evidence":[]}</SCORE_RESULT>'
        )
        self.assertEqual(decision.level_code, "UNKNOWN")
        self.assertIn("客户等级：UNKNOWN", render_score_message(decision, language="zh"))

    def test_score_message_defaults_to_english(self):
        decision = parse_score_result(
            '<SCORE_RESULT>{"score":55,"score_delta":5,"level_code":"E",'
            '"matched_points":[{"code":"E_USAGE","name":"Clear usage","score":5}],'
            '"user_evidence":[{"rule_code":"E_USAGE","quote":"The house has no signal"}]}'
            '</SCORE_RESULT>'
        )
        english = render_score_message(decision)
        self.assertIn("Score result", english)
        self.assertIn("Current score: 55 points", english)
        self.assertIn("The house has no signal", english)

    def test_invalid_score_result_is_rejected(self):
        with self.assertRaises(ScoreParseError):
            parse_score_result("no score marker")


if __name__ == "__main__":
    unittest.main()
