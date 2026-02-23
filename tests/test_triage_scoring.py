import unittest

from triage.scoring import estimate_tokens, score_from_features, select_under_budgets


class TriageScoringTest(unittest.TestCase):
    def test_estimate_tokens_is_stable(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertGreaterEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("a" * 8), 2)

    def test_score_from_features_clamps(self):
        score = score_from_features(
            {
                "text_quality": {"char_len": 10, "non_ws_ratio": 0.0, "punctuation_ratio": 1.0, "max_repeated_char_run": 20},
                "structure": {},
                "entity_hints": {},
                "event_hints": {},
                "domain_keywords": {"keyword_hit_total": 1000},
            }
        )
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_select_under_budgets_deterministic_tiebreak(self):
        chunks = [
            {
                "chunk_id": "f:2-2",
                "file_id": "f",
                "text": "warrant affidavit",
                "features": {"domain_keywords": {"keyword_hit_total": 2}},
            },
            {
                "chunk_id": "f:1-1",
                "file_id": "f",
                "text": "warrant affidavit",
                "features": {"domain_keywords": {"keyword_hit_total": 2}},
            },
        ]
        decision = select_under_budgets(chunks, max_llm_chunks=1)
        self.assertEqual(decision.selected_chunk_ids, ["f:1-1"])

    def test_select_under_budgets_per_file_cap(self):
        chunks = [
            {"chunk_id": "a:0-0", "file_id": "a", "text": "warrant", "features": {"domain_keywords": {"keyword_hit_total": 1}}},
            {"chunk_id": "a:1-1", "file_id": "a", "text": "warrant", "features": {"domain_keywords": {"keyword_hit_total": 1}}},
            {"chunk_id": "b:0-0", "file_id": "b", "text": "warrant", "features": {"domain_keywords": {"keyword_hit_total": 1}}},
        ]
        decision = select_under_budgets(chunks, max_llm_chunks=3, max_llm_chunks_per_file=1)
        self.assertEqual(len(decision.selected_chunk_ids), 2)
        self.assertIn("a:0-0", decision.selected_chunk_ids + decision.skipped_chunk_ids)

    def test_select_under_budgets_token_cap(self):
        chunks = [
            {"chunk_id": "a:0-0", "file_id": "a", "text": "x" * 100, "features": {"domain_keywords": {"keyword_hit_total": 10}}},
            {"chunk_id": "a:1-1", "file_id": "a", "text": "y" * 100, "features": {"domain_keywords": {"keyword_hit_total": 9}}},
        ]
        # Each chunk estimates to 25 tokens; cap to allow only one.
        decision = select_under_budgets(chunks, max_llm_tokens=25)
        self.assertEqual(decision.selected_total, 1)
        self.assertEqual(decision.selected_tokens_est, 25)

    def test_allow_and_deny_lists(self):
        chunks = [
            {"chunk_id": "a:0-0", "file_id": "a", "text": "warrant", "features": {"domain_keywords": {"keyword_hit_total": 1}}},
            {"chunk_id": "b:0-0", "file_id": "b", "text": "warrant", "features": {"domain_keywords": {"keyword_hit_total": 1}}},
        ]
        decision = select_under_budgets(chunks, allow_file_ids={"a"})
        self.assertEqual(decision.selected_chunk_ids, ["a:0-0"])

        decision = select_under_budgets(chunks, deny_file_ids={"a"})
        self.assertEqual(decision.selected_chunk_ids, ["b:0-0"])


if __name__ == "__main__":
    unittest.main()

