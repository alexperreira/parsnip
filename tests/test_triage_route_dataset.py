import json
import tempfile
import unittest
from pathlib import Path

from triage.build_route_dataset import build_route_dataset
from llm.cache import chunk_text_hash


class RouteDatasetBuilderTest(unittest.TestCase):
    def _write_jsonl(self, path: Path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def test_build_route_dataset_labels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks = root / "chunks.jsonl"
            triage = root / "triage.jsonl"
            entities = root / "entities.jsonl"
            events = root / "events.jsonl"
            conversations = root / "conversations.jsonl"
            identity = root / "identity_signals.jsonl"
            out = root / "ml" / "route_dataset.jsonl"

            self._write_jsonl(
                chunks,
                [
                    {"file_id": "a", "chunk_id": "a:0-0", "page_start": 0, "page_end": 0, "text": "warrant affidavit"},
                    {"file_id": "a", "chunk_id": "a:1-1", "page_start": 1, "page_end": 1, "text": "   "},
                ],
            )
            self._write_jsonl(
                triage,
                [
                    {"file_id": "a", "chunk_id": "a:0-0", "score": 0.9, "route": "llm_large", "token_est": 10, "features": {"text_quality": {"char_len": 50}}},
                    {"file_id": "a", "chunk_id": "a:1-1", "score": 0.0, "route": "skip", "token_est": 1, "features": {"text_quality": {"char_len": 0, "non_ws_ratio": 0.0}}},
                ],
            )
            self._write_jsonl(
                entities,
                [
                    {"chunk_id": "a:0-0", "items": [{"entity": "X"}], "error": None, "model": "m"},
                    {"chunk_id": "a:1-1", "items": [], "error": None, "model": "m"},
                ],
            )
            self._write_jsonl(events, [])
            self._write_jsonl(conversations, [])
            self._write_jsonl(identity, [])

            summary = build_route_dataset(
                chunks_path=chunks,
                triage_path=triage,
                entities_path=entities,
                events_path=events,
                conversations_path=conversations,
                identity_signals_path=identity,
                output_path=out,
                include_features=True,
            )
            self.assertEqual(summary["rows_written"], 2)

            rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            by_id = {row["chunk_id"]: row for row in rows}
            self.assertEqual(by_id["a:0-0"]["labels"]["label_route"], "llm_small")
            self.assertEqual(by_id["a:1-1"]["labels"]["label_route"], "skip")

    def test_build_route_dataset_applies_human_label_overrides(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks = root / "chunks.jsonl"
            triage = root / "triage.jsonl"
            entities = root / "entities.jsonl"
            events = root / "events.jsonl"
            conversations = root / "conversations.jsonl"
            identity = root / "identity_signals.jsonl"
            labels = root / "labels.jsonl"
            out = root / "ml" / "route_dataset.jsonl"

            text = "sparse text"
            self._write_jsonl(
                chunks,
                [
                    {"file_id": "a", "chunk_id": "a:0-0", "page_start": 0, "page_end": 0, "text": text},
                ],
            )
            self._write_jsonl(
                triage,
                [
                    {
                        "file_id": "a",
                        "chunk_id": "a:0-0",
                        "score": 0.02,
                        "route": "skip",
                        "token_est": 3,
                        "features": {"text_quality": {"char_len": 11}},
                    },
                ],
            )
            self._write_jsonl(entities, [])
            self._write_jsonl(events, [])
            self._write_jsonl(conversations, [])
            self._write_jsonl(identity, [])
            self._write_jsonl(
                labels,
                [
                    {
                        "chunk_id": "a:0-0",
                        "chunk_text_hash": chunk_text_hash(text),
                        "label_route": "llm_large",
                        "label_source": "human",
                    }
                ],
            )

            summary = build_route_dataset(
                chunks_path=chunks,
                triage_path=triage,
                entities_path=entities,
                events_path=events,
                conversations_path=conversations,
                identity_signals_path=identity,
                output_path=out,
                labels_path=labels,
                include_features=True,
            )
            self.assertEqual(summary["rows_written"], 1)

            row = json.loads(out.read_text(encoding="utf-8").strip())
            self.assertEqual(row["labels"]["label_route"], "llm_large")
            self.assertEqual(row["labels"]["label_source"], "human")


if __name__ == "__main__":
    unittest.main()
