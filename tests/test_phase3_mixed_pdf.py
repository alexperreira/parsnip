import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from text_extraction.phase3_extract_text import build_phase3


class Phase3MixedPdfTest(unittest.TestCase):
    def test_mixed_merge_and_quality_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_root = root / "input"
            input_root.mkdir(parents=True, exist_ok=True)
            phase1 = root / "phase1.jsonl"
            phase2 = root / "phase2.jsonl"
            out_dir = root / "output" / "text"
            phase1.write_text(
                json.dumps({"file_id": "a", "ext": "pdf", "classification": "mixed", "page_count": 2, "source_type": "fs", "virtual_path": "a.pdf"})
                + "\n"
                + json.dumps({"file_id": "b", "ext": "pdf", "classification": "mixed", "page_count": 2, "source_type": "fs", "virtual_path": "b.pdf"})
                + "\n",
                encoding="utf-8",
            )
            phase2.write_text(
                json.dumps({"file_id": "a", "pages": [{"page_index": 0, "text": "ocr a0", "confidence": 0.1}, {"page_index": 1, "text": "ocr page one from scan", "confidence": 0.9}]})
                + "\n"
                + json.dumps({"file_id": "b", "pages": [{"page_index": 0, "ocr_decision": "skip_no_image"}, {"page_index": 1, "text_path": "/missing/path.txt", "ocr_decision": "ocr"}]})
                + "\n",
                encoding="utf-8",
            )
            pdf_pages = [
                [{"page_index": 0, "text": "P" * 90}, {"page_index": 1, "text": "tiny"}],
                [{"page_index": 0, "text": "tiny"}, {"page_index": 1, "text": "tiny"}],
            ]
            with mock.patch("text_extraction.phase3_extract_text._extract_pdf_text", side_effect=pdf_pages):
                summary = build_phase3(input_root, phase1, out_dir, phase2_path=phase2, shard_size=10, resume=False, compression="none")
            self.assertEqual(summary["written"], 2)

            output = [json.loads(line) for line in (out_dir / "docs_0001.jsonl").read_text(encoding="utf-8").splitlines()]
            pages_a = output[0]["pages"]
            self.assertEqual((pages_a[0]["source"], pages_a[1]["source"]), ("pdf_text", "ocr"))
            self.assertEqual((pages_a[0]["text"], pages_a[1]["text"]), ("P" * 90, "ocr page one from scan"))

            pages_b = output[1]["pages"]
            self.assertEqual(pages_b[0]["review_reason"], "missing_mixed_text_sources")
            self.assertEqual(pages_b[1]["review_reason"], "unreadable_text_path")

            for record in output:
                avg = round(sum(page["quality_score_page"] for page in record["pages"]) / len(record["pages"]), 6)
                self.assertEqual(record["quality_score"], avg)
                for page in record["pages"]:
                    self.assertIn("quality_score_page", page)
                    self.assertIn("quality_flags", page)
                    self.assertIsInstance(page["text_char_count"], int)
                    self.assertGreaterEqual(page["quality_score_page"], 0.0)
                    self.assertLessEqual(page["quality_score_page"], 1.0)


if __name__ == "__main__":
    unittest.main()
