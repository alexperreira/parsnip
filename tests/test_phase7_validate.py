import gzip
import json
import tempfile
import unittest
from pathlib import Path

from file_parser import compress_io
from file_parser.phase7_validate import build_phase7


class Phase7ValidateTest(unittest.TestCase):
    def _write_jsonl(self, path, records):
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                if isinstance(record, str):
                    handle.write(record + "\n")
                else:
                    handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def _write_jsonl_gz(self, path, records):
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            for record in records:
                if isinstance(record, str):
                    handle.write(record + "\n")
                else:
                    handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def _write_jsonl_zst(self, path, records):
        with compress_io.open_text_writer(path) as handle:
            for record in records:
                if isinstance(record, str):
                    handle.write(record + "\n")
                else:
                    handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def test_build_phase7_metrics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            entities_path = root / "entities.jsonl"
            events_path = root / "events.jsonl"
            conversations_path = root / "conversations.jsonl"
            phase3_dir = root / "text"
            phase3_dir.mkdir(parents=True, exist_ok=True)
            shard_path = phase3_dir / "docs_0001.jsonl.gz"

            self._write_jsonl(
                chunks_path,
                [
                    {"chunk_id": "a:0-0", "file_id": "a"},
                    "{bad json",
                    {"chunk_id": "a:1-1", "file_id": "a"},
                    {"chunk_id": "b:0-0", "file_id": "b"},
                ],
            )
            self._write_jsonl(
                entities_path,
                [
                    {"chunk_id": "a:0-0", "items": [{"entity": "Alice"}], "error": None},
                    {"chunk_id": "a:1-1", "items": [], "error": None},
                    {"chunk_id": "b:0-0", "items": [{"entity": "Bob"}], "error": "invalid_json"},
                ],
            )
            self._write_jsonl(
                events_path,
                [
                    {"chunk_id": "a:0-0", "items": [], "error": None},
                    {"chunk_id": "a:1-1", "items": [{"event": "Meeting"}], "error": None},
                    {"chunk_id": "b:0-0", "items": [], "error": None},
                ],
            )
            self._write_jsonl(
                conversations_path,
                [
                    {"chunk_id": "a:0-0", "items": [], "error": "invalid_json"},
                    {"chunk_id": "a:1-1", "items": [], "error": None},
                ],
            )
            self._write_jsonl_gz(
                shard_path,
                [
                    {
                        "file_id": "a",
                        "pages": [
                            {"page_index": 0, "text": "alpha", "source": "pdf_text", "quality_flags": []},
                            {"page_index": 1, "text": "  ", "source": "ocr", "quality_flags": ["empty_text"]},
                        ],
                    },
                    {
                        "file_id": "b",
                        "pages": [
                            {"page_index": 0, "text": "", "source": "ocr", "quality_flags": ["empty_text", "ocr_error"]},
                            {"page_index": 1, "text": "omega", "source": "ocr", "quality_flags": []},
                        ],
                    },
                ],
            )

            summary = build_phase7(
                chunks_path=chunks_path,
                entities_path=entities_path,
                events_path=events_path,
                phase3_path=phase3_dir,
                conversations_path=conversations_path,
            )

            self.assertEqual(summary["total_chunks"], 3)
            self.assertEqual(summary["chunks_with_entities"], 2)
            self.assertEqual(summary["chunks_with_events"], 1)
            self.assertEqual(summary["entity_yield_pct"], 66.667)
            self.assertEqual(summary["event_yield_pct"], 33.333)
            self.assertEqual(summary["entity_records"], 3)
            self.assertEqual(summary["event_records"], 3)
            self.assertEqual(summary["llm_total_records"], 8)
            self.assertEqual(summary["llm_invalid_json_records"], 2)
            self.assertEqual(summary["llm_invalid_json_rate_pct"], 25.0)
            self.assertEqual(summary["phase3_total_pages"], 4)
            self.assertEqual(summary["phase3_empty_text_pages"], 2)
            self.assertEqual(summary["empty_text_page_rate_pct"], 50.0)
            self.assertEqual(summary["phase3_pages_pdf_text"], 1)
            self.assertEqual(summary["phase3_pages_ocr"], 3)
            self.assertEqual(summary["phase3_pages_low_quality"], 2)
            self.assertEqual(summary["phase3_ocr_page_rate_pct"], 75.0)
            self.assertEqual(summary["phase3_low_quality_page_rate_pct"], 50.0)
            self.assertEqual(summary["json_decode_errors"]["chunks"], 1)
            self.assertEqual(len(summary["warnings"]), 1)
            self.assertIn("phase3_low_quality_page_rate_high", summary["warnings"][0])

    def test_build_phase7_warns_on_chunk_record_mismatch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            entities_path = root / "entities.jsonl"
            events_path = root / "events.jsonl"
            phase3_dir = root / "text"
            phase3_dir.mkdir(parents=True, exist_ok=True)

            self._write_jsonl(
                chunks_path,
                [
                    {"chunk_id": "a:0-0"},
                    {"chunk_id": "a:1-1"},
                    {"chunk_id": "b:0-0"},
                ],
            )
            self._write_jsonl(entities_path, [{"chunk_id": "a:0-0", "items": []}])
            self._write_jsonl(
                events_path,
                [
                    {"chunk_id": "a:0-0", "items": []},
                    {"chunk_id": "a:1-1", "items": []},
                ],
            )
            self._write_jsonl_gz(
                phase3_dir / "docs_0001.jsonl.gz",
                [{"file_id": "a", "pages": [{"page_index": 0, "text": "x"}]}],
            )

            summary = build_phase7(
                chunks_path=chunks_path,
                entities_path=entities_path,
                events_path=events_path,
                phase3_path=phase3_dir,
            )

            self.assertEqual(summary["total_chunks"], 3)
            self.assertEqual(summary["entity_records"], 1)
            self.assertEqual(summary["event_records"], 2)
            self.assertEqual(len(summary["warnings"]), 2)
            self.assertIn("entities_record_count_mismatch", summary["warnings"][0])
            self.assertIn("events_record_count_mismatch", summary["warnings"][1])

    @unittest.skipUnless(compress_io._zstandard is not None, "zstandard dependency is required")
    def test_build_phase7_reads_zst_shards(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            entities_path = root / "entities.jsonl"
            events_path = root / "events.jsonl"
            phase3_dir = root / "text"
            phase3_dir.mkdir(parents=True, exist_ok=True)

            self._write_jsonl(chunks_path, [{"chunk_id": "a:0-0", "file_id": "a"}])
            self._write_jsonl(entities_path, [{"chunk_id": "a:0-0", "items": []}])
            self._write_jsonl(events_path, [{"chunk_id": "a:0-0", "items": []}])
            self._write_jsonl_zst(
                phase3_dir / "docs_0001.jsonl.zst",
                [{"file_id": "a", "pages": [{"page_index": 0, "text": "x"}]}],
            )

            summary = build_phase7(
                chunks_path=chunks_path,
                entities_path=entities_path,
                events_path=events_path,
                phase3_path=phase3_dir,
            )
            self.assertEqual(summary["phase3_docs"], 1)
            self.assertEqual(summary["phase3_total_pages"], 1)
            self.assertEqual(summary["phase3_empty_text_pages"], 0)

    def test_build_phase7_missing_required_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            chunks_path.write_text("", encoding="utf-8")
            entities_path = root / "entities.jsonl"
            entities_path.write_text("", encoding="utf-8")
            phase3_dir = root / "text"
            phase3_dir.mkdir(parents=True, exist_ok=True)
            self._write_jsonl_gz(phase3_dir / "docs_0001.jsonl.gz", [])

            with self.assertRaises(SystemExit):
                build_phase7(
                    chunks_path=chunks_path,
                    entities_path=entities_path,
                    events_path=root / "events-missing.jsonl",
                    phase3_path=phase3_dir,
                )

    def test_build_phase7_manifest_missing_shard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            entities_path = root / "entities.jsonl"
            events_path = root / "events.jsonl"
            phase3_dir = root / "text"
            phase3_dir.mkdir(parents=True, exist_ok=True)

            self._write_jsonl(chunks_path, [{"chunk_id": "a", "items": []}])
            self._write_jsonl(entities_path, [{"chunk_id": "a", "items": []}])
            self._write_jsonl(events_path, [{"chunk_id": "a", "items": []}])
            (phase3_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "shard_size": 1,
                        "shards": [{"shard": "docs_0001.jsonl.gz"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(SystemExit):
                build_phase7(
                    chunks_path=chunks_path,
                    entities_path=entities_path,
                    events_path=events_path,
                    phase3_path=phase3_dir,
                )

    def test_build_phase7_low_quality_warning_threshold(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            entities_path = root / "entities.jsonl"
            events_path = root / "events.jsonl"
            phase3_dir = root / "text"
            phase3_dir.mkdir(parents=True, exist_ok=True)

            self._write_jsonl(chunks_path, [{"chunk_id": "a:0-0", "file_id": "a"}])
            self._write_jsonl(entities_path, [{"chunk_id": "a:0-0", "items": []}])
            self._write_jsonl(events_path, [{"chunk_id": "a:0-0", "items": []}])

            high_path = phase3_dir / "docs_0001.jsonl.gz"
            self._write_jsonl_gz(
                high_path,
                [
                    {
                        "file_id": "a",
                        "pages": [
                            {"page_index": 0, "text": "good", "source": "pdf_text", "quality_flags": []},
                            {"page_index": 1, "text": "", "source": "ocr", "quality_flags": ["empty_text"]},
                            {"page_index": 2, "text": "", "source": "ocr", "quality_flags": ["empty_text"]},
                            {"page_index": 3, "text": "", "source": "ocr", "quality_flags": ["low_text"]},
                            {"page_index": 4, "text": "good", "source": "ocr", "quality_flags": []},
                            {"page_index": 5, "text": "good", "source": "ocr", "quality_flags": []},
                            {"page_index": 6, "text": "good", "source": "ocr", "quality_flags": []},
                            {"page_index": 7, "text": "good", "source": "ocr", "quality_flags": []},
                            {"page_index": 8, "text": "good", "source": "ocr", "quality_flags": []},
                            {"page_index": 9, "text": "good", "source": "ocr", "quality_flags": []},
                        ],
                    }
                ],
            )
            summary = build_phase7(
                chunks_path=chunks_path,
                entities_path=entities_path,
                events_path=events_path,
                phase3_path=phase3_dir,
            )
            self.assertEqual(summary["phase3_low_quality_page_rate_pct"], 30.0)
            self.assertEqual(summary["warnings"], [])

            self._write_jsonl_gz(
                high_path,
                [
                    {
                        "file_id": "a",
                        "pages": [
                            {"page_index": 0, "text": "good", "source": "pdf_text", "quality_flags": []},
                            {"page_index": 1, "text": "", "source": "ocr", "quality_flags": ["empty_text"]},
                            {"page_index": 2, "text": "", "source": "ocr", "quality_flags": ["empty_text"]},
                            {"page_index": 3, "text": "", "source": "ocr", "quality_flags": ["low_text"]},
                            {"page_index": 4, "text": "", "source": "ocr", "quality_flags": ["ocr_error"]},
                            {"page_index": 5, "text": "good", "source": "ocr", "quality_flags": []},
                            {"page_index": 6, "text": "good", "source": "ocr", "quality_flags": []},
                            {"page_index": 7, "text": "good", "source": "ocr", "quality_flags": []},
                            {"page_index": 8, "text": "good", "source": "ocr", "quality_flags": []},
                            {"page_index": 9, "text": "good", "source": "ocr", "quality_flags": []},
                        ],
                    }
                ],
            )
            summary = build_phase7(
                chunks_path=chunks_path,
                entities_path=entities_path,
                events_path=events_path,
                phase3_path=phase3_dir,
            )
            self.assertEqual(summary["phase3_low_quality_page_rate_pct"], 40.0)
            self.assertEqual(len(summary["warnings"]), 1)
            self.assertIn("phase3_low_quality_page_rate_high", summary["warnings"][0])


if __name__ == "__main__":
    unittest.main()
