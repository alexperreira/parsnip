import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from file_parser.phase2_ocr import build_phase2


class Phase2SkeletonTest(unittest.TestCase):
    def test_phase2_filters_scanned_and_mixed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            phase1_path = root / "phase1.jsonl"
            records = [
                {"file_id": "a", "ext": "pdf", "classification": "scanned"},
                {"file_id": "b", "ext": "pdf", "classification": "mixed"},
                {"file_id": "c", "ext": "pdf", "classification": "text"},
                {"file_id": "d", "ext": "pdf", "classification": "unknown"},
            ]
            phase1_path.write_text(
                "\n".join(json.dumps(r) for r in records) + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"

            summary = build_phase2(
                root,
                phase1_path,
                output_path,
                resume=False,
                engine="noop",
            )
            self.assertEqual(summary["written"], 2)

            output_records = [
                json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()
            ]
            ids = {record["file_id"] for record in output_records}
            self.assertEqual(ids, {"a", "b"})

    def test_phase2_resume(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            phase1_path = root / "phase1.jsonl"
            phase1_path.write_text(
                json.dumps({"file_id": "a", "ext": "pdf", "classification": "scanned"}) + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"

            build_phase2(root, phase1_path, output_path, resume=False, engine="noop")
            summary = build_phase2(root, phase1_path, output_path, resume=True, engine="noop")
            self.assertEqual(summary["written"], 0)
            self.assertEqual(summary["skipped"], 1)

    def test_phase2_workers_with_noop_engine(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            phase1_path = root / "phase1.jsonl"
            records = [
                {"file_id": "a", "ext": "pdf", "classification": "scanned"},
                {"file_id": "b", "ext": "pdf", "classification": "mixed"},
            ]
            phase1_path.write_text(
                "\n".join(json.dumps(r) for r in records) + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"

            summary = build_phase2(
                root,
                phase1_path,
                output_path,
                resume=False,
                engine="noop",
                workers=2,
            )
            self.assertEqual(summary["written"], 2)

    def test_phase2_ordered_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            phase1_path = root / "phase1.jsonl"
            records = [
                {"file_id": "a", "ext": "pdf", "classification": "scanned"},
                {"file_id": "b", "ext": "pdf", "classification": "mixed"},
                {"file_id": "c", "ext": "pdf", "classification": "scanned"},
            ]
            phase1_path.write_text(
                "\n".join(json.dumps(r) for r in records) + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"

            build_phase2(
                root,
                phase1_path,
                output_path,
                resume=False,
                engine="noop",
                workers=2,
                ordered=True,
            )

            output_records = [
                json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([r["file_id"] for r in output_records], ["a", "b", "c"])

    def test_phase2_page_workers_cap_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            phase1_path = root / "phase1.jsonl"
            phase1_path.write_text(
                json.dumps({"file_id": "a", "ext": "pdf", "classification": "scanned"}) + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"
            buffer = io.StringIO()
            with mock.patch("file_parser.phase2_ocr.os.cpu_count", return_value=4):
                with contextlib.redirect_stdout(buffer):
                    build_phase2(
                        root,
                        phase1_path,
                        output_path,
                        resume=False,
                        engine="noop",
                        workers=2,
                        page_workers=4,
                    )
            output = buffer.getvalue()
            self.assertIn("capping page_workers", output)

    def test_phase2_worker_oversubscription_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            phase1_path = root / "phase1.jsonl"
            phase1_path.write_text(
                json.dumps({"file_id": "a", "ext": "pdf", "classification": "scanned"}) + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"
            buffer = io.StringIO()
            with mock.patch("file_parser.phase2_ocr.os.cpu_count", return_value=1):
                with contextlib.redirect_stdout(buffer):
                    build_phase2(
                        root,
                        phase1_path,
                        output_path,
                        resume=False,
                        engine="noop",
                        workers=2,
                        page_workers=1,
                    )
            output = buffer.getvalue()
            self.assertIn("exceeds CPU count", output)


if __name__ == "__main__":
    unittest.main()
