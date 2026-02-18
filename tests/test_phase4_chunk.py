import json
import tempfile
import unittest
from pathlib import Path

from chunking.phase4_chunk import build_phase4
from file_parser import compress_io


class Phase4ChunkTest(unittest.TestCase):
    def _write_shard(self, path, records):
        with compress_io.open_text_writer(path) as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def _read_jsonl(self, path):
        with path.open("r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_build_phase4_reads_gzip_shards(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_dir = root / "text"
            input_dir.mkdir(parents=True, exist_ok=True)
            output_path = root / "chunks.jsonl"

            self._write_shard(
                input_dir / "docs_0001.jsonl.gz",
                [
                    {
                        "file_id": "file_a",
                        "pages": [
                            {"page_index": 0, "text": "alpha"},
                            {"page_index": 1, "text": "omega"},
                        ],
                    }
                ],
            )

            summary = build_phase4(
                input_path=input_dir,
                output_path=output_path,
                chunk_size=2,
                overlap=1,
                overwrite=False,
                append=False,
            )
            rows = self._read_jsonl(output_path)

            self.assertEqual(summary["docs_seen"], 1)
            self.assertGreater(summary["chunks_written"], 0)
            self.assertEqual(len(rows), summary["chunks_written"])

    @unittest.skipUnless(compress_io._zstandard is not None, "zstandard dependency is required")
    def test_build_phase4_reads_zstd_shards(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_dir = root / "text"
            input_dir.mkdir(parents=True, exist_ok=True)
            output_path = root / "chunks.jsonl"

            self._write_shard(
                input_dir / "docs_0001.jsonl.zst",
                [
                    {
                        "file_id": "file_a",
                        "pages": [
                            {"page_index": 0, "text": "alpha"},
                            {"page_index": 1, "text": "omega"},
                        ],
                    }
                ],
            )

            summary = build_phase4(
                input_path=input_dir,
                output_path=output_path,
                chunk_size=2,
                overlap=1,
                overwrite=False,
                append=False,
            )
            rows = self._read_jsonl(output_path)

            self.assertEqual(summary["docs_seen"], 1)
            self.assertGreater(summary["chunks_written"], 0)
            self.assertEqual(len(rows), summary["chunks_written"])


if __name__ == "__main__":
    unittest.main()
