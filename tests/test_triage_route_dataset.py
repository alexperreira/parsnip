import json
import sqlite3
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
                entities_large_path=None,
                events_large_path=None,
                conversations_large_path=None,
                identity_signals_large_path=None,
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
                entities_large_path=None,
                events_large_path=None,
                conversations_large_path=None,
                identity_signals_large_path=None,
                output_path=out,
                labels_path=labels,
                include_features=True,
            )
            self.assertEqual(summary["rows_written"], 1)

            row = json.loads(out.read_text(encoding="utf-8").strip())
            self.assertEqual(row["labels"]["label_route"], "llm_large")
            self.assertEqual(row["labels"]["label_source"], "human")

    def test_build_route_dataset_prefers_empirical_large_yield(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks = root / "chunks.jsonl"
            triage = root / "triage.jsonl"
            entities = root / "entities.jsonl"
            events = root / "events.jsonl"
            conversations = root / "conversations.jsonl"
            identity = root / "identity_signals.jsonl"
            entities_large = root / "entities.llm_large.jsonl"
            out = root / "ml" / "route_dataset.jsonl"

            self._write_jsonl(
                chunks,
                [
                    {"file_id": "a", "chunk_id": "a:0-0", "page_start": 0, "page_end": 0, "text": "dense appendix"},
                ],
            )
            self._write_jsonl(
                triage,
                [
                    {
                        "file_id": "a",
                        "chunk_id": "a:0-0",
                        "score": 0.5,
                        "route": "llm_large",
                        "token_est": 20,
                        "features": {"text_quality": {"char_len": 100}},
                    }
                ],
            )
            self._write_jsonl(entities, [])
            self._write_jsonl(events, [])
            self._write_jsonl(conversations, [])
            self._write_jsonl(identity, [])
            self._write_jsonl(
                entities_large,
                [
                    {"chunk_id": "a:0-0", "items": [{"entity": "X"}], "error": None, "model": "m-large"},
                ],
            )

            summary = build_route_dataset(
                chunks_path=chunks,
                triage_path=triage,
                entities_path=entities,
                events_path=events,
                conversations_path=conversations,
                identity_signals_path=identity,
                entities_large_path=entities_large,
                events_large_path=None,
                conversations_large_path=None,
                identity_signals_large_path=None,
                output_path=out,
                include_features=True,
            )
            self.assertEqual(summary["rows_written"], 1)
            self.assertEqual(summary["rows_labeled_empirical_large"], 1)

            row = json.loads(out.read_text(encoding="utf-8").strip())
            self.assertEqual(row["labels"]["label_route"], "llm_large")
            self.assertEqual(row["labels"]["label_source"], "empirical_large_yield")
            self.assertTrue(row["derived"]["any_large_yield"])
            self.assertIn("outcomes_large", row)

    def test_build_route_dataset_uses_downstream_utility_labels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks = root / "chunks.jsonl"
            triage = root / "triage.jsonl"
            entities = root / "entities.jsonl"
            events = root / "events.jsonl"
            conversations = root / "conversations.jsonl"
            identity = root / "identity_signals.jsonl"
            db_path = root / "store.sqlite"
            out = root / "ml" / "route_dataset.jsonl"

            self._write_jsonl(
                chunks,
                [
                    {
                        "file_id": "a",
                        "chunk_id": "a:0-0",
                        "page_start": 0,
                        "page_end": 0,
                        "text": "sparse chunk with little direct yield",
                    },
                ],
            )
            self._write_jsonl(
                triage,
                [
                    {
                        "file_id": "a",
                        "chunk_id": "a:0-0",
                        "score": 0.4,
                        "route": "llm_small",
                        "token_est": 8,
                        "features": {"text_quality": {"char_len": 40}},
                    }
                ],
            )
            self._write_jsonl(entities, [])
            self._write_jsonl(events, [])
            self._write_jsonl(conversations, [])
            self._write_jsonl(identity, [])

            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "CREATE TABLE events ("
                    "event_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "event TEXT, date TEXT, confidence REAL, "
                    "file_id TEXT NOT NULL, chunk_id TEXT NOT NULL, "
                    "page_start INTEGER, page_end INTEGER, quote TEXT)"
                )
                conn.execute(
                    "CREATE TABLE event_times ("
                    "event_id INTEGER PRIMARY KEY,"
                    "date_raw TEXT, date_start TEXT, date_end TEXT, precision TEXT, "
                    "status TEXT NOT NULL, parser TEXT, anchor_date TEXT, notes_json TEXT)"
                )
                conn.execute(
                    "CREATE TABLE person_observations ("
                    "obs_id INTEGER PRIMARY KEY,"
                    "name TEXT NOT NULL, name_norm TEXT NOT NULL, "
                    "file_id TEXT NOT NULL, chunk_id TEXT NOT NULL, page_start INTEGER, page_end INTEGER)"
                )
                conn.execute(
                    "CREATE TABLE person_cluster_members ("
                    "person_id INTEGER NOT NULL, obs_id INTEGER NOT NULL, PRIMARY KEY(person_id, obs_id))"
                )
                conn.execute(
                    "INSERT INTO events(event_id, event, file_id, chunk_id) VALUES (1, 'incident', 'a', 'a:0-0')"
                )
                conn.execute(
                    "INSERT INTO event_times(event_id, status, date_start) VALUES (1, 'ok', '2025-01-02')"
                )
                conn.commit()
            finally:
                conn.close()

            summary = build_route_dataset(
                chunks_path=chunks,
                triage_path=triage,
                entities_path=entities,
                events_path=events,
                conversations_path=conversations,
                identity_signals_path=identity,
                entities_large_path=None,
                events_large_path=None,
                conversations_large_path=None,
                identity_signals_large_path=None,
                output_path=out,
                db_path=db_path,
                include_features=True,
            )
            self.assertEqual(summary["rows_written"], 1)
            self.assertEqual(summary["rows_labeled_downstream_utility"], 1)

            row = json.loads(out.read_text(encoding="utf-8").strip())
            self.assertEqual(row["labels"]["label_route"], "llm_small")
            self.assertEqual(row["labels"]["label_source"], "downstream_utility")
            self.assertEqual(row["downstream_utility"]["timeline_ok_events"], 1)
            self.assertTrue(row["derived"]["downstream_utility_positive"])


if __name__ == "__main__":
    unittest.main()
