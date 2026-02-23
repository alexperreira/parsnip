import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class LLMCacheIntegrationTest(unittest.TestCase):
    def _write_chunks(self, path: Path, records):
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def test_entities_cache_reuses_success(self):
        from llm import extract_entities

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks = root / "chunks.jsonl"
            out = root / "entities.jsonl"
            self._write_chunks(
                chunks,
                [
                    {
                        "file_id": "a",
                        "chunk_id": "a:0-0",
                        "page_start": 0,
                        "page_end": 0,
                        "text": "Alice met Bob.",
                    }
                ],
            )

            with mock.patch.object(
                extract_entities,
                "call_llm",
                return_value='{"items":[{"entity":"Alice","type":"PERSON","quote":"Alice","confidence":0.9}]}',
            ) as call_mock:
                argv = [
                    "extract_entities",
                    "--input",
                    str(chunks),
                    "--output",
                    str(out),
                    "--cache",
                ]
                with mock.patch("sys.argv", argv):
                    extract_entities.main()
                self.assertEqual(call_mock.call_count, 1)

            with mock.patch.object(extract_entities, "call_llm", side_effect=AssertionError("cache miss")) as call_mock:
                argv = [
                    "extract_entities",
                    "--input",
                    str(chunks),
                    "--output",
                    str(out),
                    "--cache",
                ]
                with mock.patch("sys.argv", argv):
                    extract_entities.main()
                self.assertEqual(call_mock.call_count, 0)

            output_records = [
                json.loads(line)
                for line in out.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(output_records), 1)
            self.assertIsNone(output_records[0]["error"])
            self.assertEqual(len(output_records[0]["items"]), 1)

    def test_cache_retry_errors(self):
        from llm import extract_entities

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks = root / "chunks.jsonl"
            out = root / "entities.jsonl"
            self._write_chunks(
                chunks,
                [
                    {
                        "file_id": "a",
                        "chunk_id": "a:0-0",
                        "page_start": 0,
                        "page_end": 0,
                        "text": "Alice met Bob.",
                    }
                ],
            )

            with mock.patch.object(extract_entities, "call_llm", side_effect=TimeoutError("nope")) as call_mock:
                argv = [
                    "extract_entities",
                    "--input",
                    str(chunks),
                    "--output",
                    str(out),
                    "--cache",
                ]
                with mock.patch("sys.argv", argv):
                    extract_entities.main()
                self.assertEqual(call_mock.call_count, 1)

            with mock.patch.object(extract_entities, "call_llm", side_effect=AssertionError("should reuse error")) as call_mock:
                argv = [
                    "extract_entities",
                    "--input",
                    str(chunks),
                    "--output",
                    str(out),
                    "--cache",
                ]
                with mock.patch("sys.argv", argv):
                    extract_entities.main()
                self.assertEqual(call_mock.call_count, 0)

            with mock.patch.object(
                extract_entities,
                "call_llm",
                return_value='{"items":[{"entity":"Alice","type":"PERSON","quote":"Alice","confidence":0.9}]}',
            ) as call_mock:
                argv = [
                    "extract_entities",
                    "--input",
                    str(chunks),
                    "--output",
                    str(out),
                    "--cache",
                    "--cache-retry-errors",
                ]
                with mock.patch("sys.argv", argv):
                    extract_entities.main()
                self.assertEqual(call_mock.call_count, 1)

            record = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
            self.assertIsNone(record["error"])


if __name__ == "__main__":
    unittest.main()

