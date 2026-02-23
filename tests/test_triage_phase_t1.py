import json
import tempfile
import unittest
from pathlib import Path

from triage.phase_t1_triage_chunks import build_triage


class TriagePhaseT1Test(unittest.TestCase):
    def _write_jsonl(self, path: Path, records):
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def test_build_triage_writes_outputs_and_budgets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            out_dir = root / "out"

            # One high-signal and one low-signal chunk.
            self._write_jsonl(
                chunks_path,
                [
                    {
                        "chunk_id": "a:0-0",
                        "file_id": "a",
                        "page_start": 0,
                        "page_end": 0,
                        "text": "warrant affidavit on 2025-01-02 at 10:35 PM",
                    },
                    {
                        "chunk_id": "a:1-1",
                        "file_id": "a",
                        "page_start": 1,
                        "page_end": 1,
                        "text": "   ",
                    },
                ],
            )

            summary = build_triage(
                chunks_path=chunks_path,
                output_dir=out_dir,
                max_llm_chunks=1,
                route_skip_threshold=0.2,
            )

            triage_path = Path(summary["triage_path"])
            small_path = Path(summary["llm_small_path"])
            large_path = Path(summary["llm_large_path"])

            self.assertTrue(triage_path.exists())
            self.assertTrue(small_path.exists())
            self.assertTrue(large_path.exists())

            triage_records = [json.loads(line) for line in triage_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(triage_records), 2)
            self.assertIn(triage_records[0]["route"], {"llm_small", "llm_large", "skip"})

            selected_small = [json.loads(line) for line in small_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            selected_large = [json.loads(line) for line in large_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(selected_small) + len(selected_large), 1)


if __name__ == "__main__":
    unittest.main()

