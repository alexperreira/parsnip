import gzip
import json
import tempfile
import unittest
from pathlib import Path

from text_extraction.phase3_extract_text import build_phase3


class Phase3ResumeTest(unittest.TestCase):
    def _write_phase1(self, path, records):
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def _phase1_record(self, file_id, virtual_path):
        return {
            "file_id": file_id,
            "ext": "pdf",
            "classification": "scanned",
            "page_count": 1,
            "source_type": "fs",
            "virtual_path": virtual_path,
        }

    def test_resume_handles_extra_shard_and_new_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_root = root / "input"
            input_root.mkdir(parents=True, exist_ok=True)
            phase1_path = root / "phase1.jsonl"
            output_dir = root / "output" / "text"

            initial_records = [
                self._phase1_record("file_a", "a.pdf"),
                self._phase1_record("file_b", "b.pdf"),
            ]
            self._write_phase1(phase1_path, initial_records)

            summary = build_phase3(
                input_root,
                phase1_path,
                output_dir,
                phase2_path=None,
                shard_size=10,
                resume=False,
            )
            self.assertEqual(summary["written"], 2)

            extra_shard = output_dir / "docs_0002.jsonl.gz"
            extra_record = {
                "file_id": "file_extra",
                "virtual_path": "extra.pdf",
                "classification": "scanned",
                "page_count": 1,
                "quality_score": 0.0,
                "pages": [],
            }
            with gzip.open(extra_shard, "wt", encoding="utf-8") as handle:
                handle.write(json.dumps(extra_record, ensure_ascii=True) + "\n")

            resumed_records = [
                self._phase1_record("file_a", "a.pdf"),
                self._phase1_record("file_b", "b.pdf"),
                self._phase1_record("file_extra", "extra.pdf"),
                self._phase1_record("file_new", "new.pdf"),
            ]
            self._write_phase1(phase1_path, resumed_records)

            summary = build_phase3(
                input_root,
                phase1_path,
                output_dir,
                phase2_path=None,
                shard_size=10,
                resume=True,
            )
            self.assertEqual(summary["written"], 1)
            self.assertEqual(summary["skipped"], 3)

            manifest_path = output_dir / "manifest.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            shard_names = [entry["shard"] for entry in payload["shards"]]
            self.assertIn("docs_0002.jsonl.gz", shard_names)
            self.assertIn("docs_0003.jsonl.gz", shard_names)

            resume_db = output_dir / "resume.db"
            self.assertTrue(resume_db.exists())

    def test_resume_fails_on_corrupt_shard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_root = root / "input"
            input_root.mkdir(parents=True, exist_ok=True)
            phase1_path = root / "phase1.jsonl"
            output_dir = root / "output" / "text"

            initial_records = [self._phase1_record("file_a", "a.pdf")]
            self._write_phase1(phase1_path, initial_records)

            summary = build_phase3(
                input_root,
                phase1_path,
                output_dir,
                phase2_path=None,
                shard_size=10,
                resume=False,
            )
            self.assertEqual(summary["written"], 1)

            corrupt_shard = output_dir / "docs_0002.jsonl.gz"
            corrupt_shard.write_bytes(b"not a gzipped payload")

            resumed_records = [
                self._phase1_record("file_a", "a.pdf"),
                self._phase1_record("file_b", "b.pdf"),
            ]
            self._write_phase1(phase1_path, resumed_records)

            with self.assertRaises(SystemExit) as ctx:
                build_phase3(
                    input_root,
                    phase1_path,
                    output_dir,
                    phase2_path=None,
                    shard_size=10,
                    resume=True,
                )
            self.assertIn("Failed to read shard", str(ctx.exception))

    def test_resume_fails_on_shard_size_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_root = root / "input"
            input_root.mkdir(parents=True, exist_ok=True)
            phase1_path = root / "phase1.jsonl"
            output_dir = root / "output" / "text"

            records = [self._phase1_record("file_a", "a.pdf")]
            self._write_phase1(phase1_path, records)

            summary = build_phase3(
                input_root,
                phase1_path,
                output_dir,
                phase2_path=None,
                shard_size=10,
                resume=False,
            )
            self.assertEqual(summary["written"], 1)

            manifest_path = output_dir / "manifest.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["shard_size"] = 3
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(SystemExit) as ctx:
                build_phase3(
                    input_root,
                    phase1_path,
                    output_dir,
                    phase2_path=None,
                    shard_size=10,
                    resume=True,
                )
            self.assertIn("Shard size mismatch", str(ctx.exception))

    def test_resume_fails_on_shard_size_mismatch_from_manifest_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_root = root / "input"
            input_root.mkdir(parents=True, exist_ok=True)
            phase1_path = root / "phase1.jsonl"
            output_dir = root / "output" / "text"
            output_dir.mkdir(parents=True, exist_ok=True)

            records = [
                self._phase1_record("file_a", "a.pdf"),
                self._phase1_record("file_b", "b.pdf"),
                self._phase1_record("file_c", "c.pdf"),
            ]
            self._write_phase1(phase1_path, records)

            shard_path = output_dir / "docs_0001.jsonl.gz"
            with gzip.open(shard_path, "wt", encoding="utf-8") as handle:
                for record in records:
                    output_record = {
                        "file_id": record["file_id"],
                        "virtual_path": record["virtual_path"],
                        "classification": "scanned",
                        "page_count": 1,
                        "quality_score": 0.0,
                        "pages": [],
                    }
                    handle.write(json.dumps(output_record, ensure_ascii=True) + "\n")

            manifest_payload = {
                "shard_size": 3,
                "shards": [
                    {
                        "shard": "docs_0001.jsonl.gz",
                        "start_index": 0,
                        "end_index": 2,
                        "doc_count": 3,
                    }
                ],
            }
            manifest_path = output_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

            with self.assertRaises(SystemExit) as ctx:
                build_phase3(
                    input_root,
                    phase1_path,
                    output_dir,
                    phase2_path=None,
                    shard_size=10,
                    resume=True,
                )
            self.assertIn("Shard size mismatch", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
