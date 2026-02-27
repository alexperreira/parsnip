import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from loaders.load_chunks import build_load_chunks


class LoadChunksTest(unittest.TestCase):
    def _write_jsonl(self, path: Path, records):
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                if isinstance(record, str):
                    handle.write(record + "\n")
                else:
                    handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def test_load_chunks_single_file_with_refs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            db_path = root / "store.sqlite"
            self._write_jsonl(
                chunks_path,
                [
                    "{invalid json",
                    {
                        "chunk_id": "file_a:1-2",
                        "file_id": "file_a",
                        "page_start": 1,
                        "page_end": 2,
                        "text": "Hello world",
                        "signals": {"has_dates": False, "has_names": True},
                    },
                ],
            )

            summary = build_load_chunks(chunks_path, db_path, overwrite=True)
            self.assertEqual(summary["files_total"], 1)
            self.assertEqual(summary["records_total"], 2)
            self.assertEqual(summary["json_decode_errors"], 1)
            self.assertEqual(summary["rows_attempted"], 1)
            self.assertEqual(summary["rows_inserted"], 1)
            self.assertEqual(summary["text_refs_upserted"], 1)

            conn = sqlite3.connect(db_path)
            chunk_row = conn.execute(
                "SELECT file_id, page_start, page_end, signals_json, text_ref FROM chunks WHERE chunk_id=?",
                ("file_a:1-2",),
            ).fetchone()
            self.assertEqual(chunk_row[0:3], ("file_a", 1, 2))
            self.assertEqual(chunk_row[3], '{"has_dates": false, "has_names": true}')
            self.assertTrue(str(chunk_row[4]).startswith("jsonl://"))

            ref_row = conn.execute(
                "SELECT storage_path, byte_start, byte_end, text_char_count, text_sha256 "
                "FROM chunk_text_refs WHERE chunk_id=?",
                ("file_a:1-2",),
            ).fetchone()
            self.assertIsNotNone(ref_row)
            self.assertTrue(str(ref_row[0]).endswith("chunks.jsonl"))
            self.assertEqual(ref_row[3], 11)
            self.assertEqual(
                ref_row[4],
                "64ec88ca00b268e5ba1a35678a1b5316d212f4f366b2477232534a8aeca37f3c",
            )
            conn.close()

    def test_load_chunks_directory_gzip_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_dir = root / "chunks_dir"
            chunks_dir.mkdir(parents=True, exist_ok=True)
            chunks_path = chunks_dir / "chunks_0001.jsonl.gz"
            db_path = root / "store.sqlite"
            records = [
                {
                    "chunk_id": "file_b:4-4",
                    "file_id": "file_b",
                    "page_start": 4,
                    "page_end": 4,
                    "text": "One line",
                },
                {
                    "chunk_id": "file_bad:6-5",
                    "file_id": "file_bad",
                    "page_start": 6,
                    "page_end": 5,
                },
            ]
            with gzip.open(chunks_path, "wt", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=True) + "\n")

            first = build_load_chunks(chunks_dir, db_path, overwrite=True)
            self.assertEqual(first["files_total"], 1)
            self.assertEqual(first["rows_inserted"], 1)
            self.assertEqual(first["invalid_page_range"], 1)

            second = build_load_chunks(chunks_dir, db_path, overwrite=False)
            self.assertEqual(second["rows_inserted"], 0)
            self.assertEqual(second["rows_skipped"], 1)
            self.assertEqual(second["text_refs_upserted"], 1)


if __name__ == "__main__":
    unittest.main()
