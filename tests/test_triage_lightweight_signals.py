import unittest

import tempfile
from pathlib import Path

from triage.lightweight_signals import compile_keyword_packs, compute_lightweight_signals
from triage.lightweight_signals import load_keyword_packs_from_dir


class TriageLightweightSignalsTest(unittest.TestCase):
    def test_text_quality_and_structure(self):
        text = "\n".join(
            [
                "Q: What time was it?",
                "A: Around 10:35 PM on 2025-01-02.",
                "",
                "- First bullet",
                "1. Second bullet",
                "Name: John Smith",
                "Dr. Jane Doe arrived.",
                "ID #A-12345",
                "Col1  Col2  Col3",
                "123   456   789",
                "!!!!!",
            ]
        )
        features = compute_lightweight_signals(text)
        tq = features["text_quality"]
        struct = features["structure"]
        entity = features["entity_hints"]
        event = features["event_hints"]

        self.assertGreaterEqual(tq["char_len"], 10)
        self.assertGreater(tq["non_ws_ratio"], 0.2)
        self.assertGreater(tq["punctuation_ratio"], 0.01)
        self.assertEqual(tq["replacement_char_count"], 0)
        self.assertGreaterEqual(tq["max_repeated_char_run"], 5)
        self.assertGreater(tq["word_shape_diversity"], 0.0)

        self.assertGreaterEqual(struct["line_count"], 10)
        self.assertGreaterEqual(struct["nonempty_line_count"], 9)
        self.assertEqual(struct["bullet_line_count"], 2)
        self.assertGreater(struct["bullet_density"], 0.0)
        self.assertTrue(struct["table_like"])
        self.assertGreaterEqual(struct["dialogue_marker_count"], 2)
        self.assertGreaterEqual(struct["timestamp_count"], 1)

        self.assertGreaterEqual(entity["honorific_count"], 1)
        self.assertGreaterEqual(entity["capitalized_name_count"], 1)
        self.assertGreaterEqual(entity["badge_id_count"], 1)

        self.assertGreaterEqual(event["date_like_count"], 1)
        self.assertGreaterEqual(event["time_like_count"], 1)
        self.assertGreaterEqual(event["on_at_by_date_count"], 1)
        self.assertGreaterEqual(event["incident_verb_count"], 1)

    def test_keyword_packs_optional(self):
        packs = {
            "case": ["warrant", "affidavit"],
            "comms": ["text message", "voicemail"],
        }
        compiled = compile_keyword_packs(packs)
        text = "Left a voicemail and later sent a text message. No affidavit was found."

        features = compute_lightweight_signals(text, compiled_keyword_packs=compiled)
        domain = features["domain_keywords"]

        self.assertEqual(domain["keyword_hit_total"], 3)
        self.assertEqual(domain["keyword_hit_by_pack"]["comms"], 2)
        self.assertEqual(domain["keyword_hit_by_pack"]["case"], 1)

    def test_load_keyword_packs_from_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "legal.txt").write_text(
                "\n".join(
                    [
                        "# comment",
                        "",
                        "warrant",
                        "affidavit",
                        "  ",
                    ]
                ),
                encoding="utf-8",
            )
            (root / "comms.txt").write_text("text message\nvoicemail\n", encoding="utf-8")

            packs = load_keyword_packs_from_dir(root)
            self.assertEqual(set(packs.keys()), {"legal", "comms"})
            self.assertEqual(packs["legal"], ["warrant", "affidavit"])

            compiled = compile_keyword_packs(packs)
            features = compute_lightweight_signals(
                "Affidavit and warrant; voicemail left.",
                compiled_keyword_packs=compiled,
            )
            domain = features["domain_keywords"]
            self.assertEqual(domain["keyword_hit_by_pack"]["legal"], 2)
            self.assertEqual(domain["keyword_hit_by_pack"]["comms"], 1)

    def test_ner_is_fail_soft(self):
        # Use an intentionally invalid model name so this is stable even if spaCy is installed.
        features = compute_lightweight_signals(
            "John Smith met Jane Doe on 2025-01-02.",
            ner_enabled=True,
            ner_model="__parsnip_no_such_spacy_model__",
        )
        ner = features["ner"]
        self.assertIn("available", ner)
        self.assertIn("counts_by_label", ner)
        self.assertFalse(ner["available"])

    def test_non_string_inputs_are_safe(self):
        features = compute_lightweight_signals(None)
        self.assertEqual(features["text_quality"]["char_len"], 0)
        self.assertEqual(features["structure"]["line_count"], 0)


if __name__ == "__main__":
    unittest.main()
