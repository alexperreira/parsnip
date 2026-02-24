import json
import tempfile
import unittest
from pathlib import Path

from triage.sample_route_labels import sample_route_labels


class RouteLabelSamplerTest(unittest.TestCase):
    def _write_jsonl(self, path: Path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def test_samples_uncertain_bands_and_redacts_preview(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_path = root / "route_dataset.jsonl"
            output_path = root / "labels_queue.jsonl"
            self._write_jsonl(
                input_path,
                [
                    {
                        "chunk_id": "a:0-0",
                        "chunk_text_hash": "h1",
                        "file_id": "a",
                        "text": "Call john@example.com or +1 (555) 123-9999.",
                        "triage": {"score": 0.06},
                        "labels": {"label_route": "skip", "label_source": "heuristic_from_yield"},
                    },
                    {
                        "chunk_id": "a:1-1",
                        "chunk_text_hash": "h2",
                        "file_id": "a",
                        "text": "Already reviewed",
                        "triage": {"score": 0.15},
                        "labels": {"label_route": "skip", "label_source": "human"},
                    },
                    {
                        "chunk_id": "b:0-0",
                        "chunk_text_hash": "h3",
                        "file_id": "b",
                        "text": "High-score uncertain chunk 2025-01-02.",
                        "triage": {"score": 0.70},
                        "labels": {"label_route": "llm_small", "label_source": "heuristic_from_yield"},
                    },
                    {
                        "chunk_id": "b:1-1",
                        "chunk_text_hash": "h4",
                        "file_id": "b",
                        "text": "Not in any uncertainty window",
                        "triage": {"score": 0.95},
                        "labels": {"label_route": "llm_large", "label_source": "heuristic_from_yield"},
                    },
                ],
            )

            summary = sample_route_labels(
                input_path=input_path,
                output_path=output_path,
                max_per_band=10,
                preview_mode="redacted",
                preview_chars=64,
                exclude_human_labeled=True,
            )
            self.assertEqual(summary["rows_scanned"], 4)
            self.assertEqual(summary["rows_excluded_human"], 1)
            self.assertEqual(summary["eligible_low_band"], 1)
            self.assertEqual(summary["eligible_high_band"], 1)
            self.assertEqual(summary["rows_written"], 2)

            rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(rows), 2)
            by_id = {row["chunk_id"]: row for row in rows}
            self.assertEqual(by_id["a:0-0"]["uncertainty_band"], "low")
            self.assertEqual(by_id["b:0-0"]["uncertainty_band"], "high")
            self.assertNotIn("@", by_id["a:0-0"]["preview_redacted"])
            self.assertNotIn("555", by_id["a:0-0"]["preview_redacted"])

    def test_preview_mode_none_omits_preview(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_path = root / "route_dataset.jsonl"
            output_path = root / "labels_queue.jsonl"
            self._write_jsonl(
                input_path,
                [
                    {
                        "chunk_id": "c:0-0",
                        "chunk_text_hash": "h5",
                        "file_id": "c",
                        "text": "Example text",
                        "triage": {"score": 0.08},
                        "labels": {"label_route": "skip", "label_source": "heuristic_from_yield"},
                    }
                ],
            )

            summary = sample_route_labels(
                input_path=input_path,
                output_path=output_path,
                preview_mode="none",
            )
            self.assertEqual(summary["rows_written"], 1)
            row = json.loads(output_path.read_text(encoding="utf-8").strip())
            self.assertNotIn("preview_redacted", row)

    def test_max_per_band_zero_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_path = root / "route_dataset.jsonl"
            output_path = root / "labels_queue.jsonl"
            self._write_jsonl(
                input_path,
                [
                    {
                        "chunk_id": "d:0-0",
                        "chunk_text_hash": "h6",
                        "file_id": "d",
                        "text": "Example",
                        "triage": {"score": 0.10},
                        "labels": {"label_route": "skip", "label_source": "heuristic_from_yield"},
                    }
                ],
            )
            summary = sample_route_labels(
                input_path=input_path,
                output_path=output_path,
                max_per_band=0,
            )
            self.assertEqual(summary["sampled_low_band"], 0)
            self.assertEqual(summary["rows_written"], 0)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
