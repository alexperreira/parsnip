import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from loaders.load_conversations import build_load_conversations
from loaders.load_entities import build_load_entities
from loaders.load_events import build_load_events
from loaders.load_identity_signals import build_load_identity_signals
from loaders.store import SCHEMA_VERSION


class Phase6LoadersTest(unittest.TestCase):
    def _write_jsonl(self, path, records):
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                if isinstance(record, str):
                    handle.write(record + "\n")
                else:
                    handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def _table_count(self, conn, table_name):
        return conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

    def test_schema_creation_and_meta(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entities_path = root / "entities.jsonl"
            db_path = root / "store.sqlite"
            self._write_jsonl(
                entities_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 2],
                        "items": [{"entity": "Alice", "type": "person", "confidence": 0.9}],
                    }
                ],
            )
            summary = build_load_entities(entities_path, db_path, overwrite=True)
            self.assertEqual(summary["rows_inserted"], 1)

            conn = sqlite3.connect(db_path)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            self.assertTrue({"entities", "events", "conversations", "mentions", "meta"}.issubset(tables))

            index_names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            self.assertIn("idx_events_date", index_names)
            self.assertIn("idx_mentions_entity", index_names)
            self.assertIn("idx_entities_provenance", index_names)
            self.assertIn("idx_identity_signals_provenance", index_names)

            entity_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(entities)").fetchall()
            }
            self.assertTrue(
                {
                    "char_start",
                    "char_end",
                    "source_phase",
                    "extractor_version",
                    "model",
                    "prompt_hash",
                }.issubset(entity_columns)
            )

            schema_version = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            self.assertIsNotNone(schema_version)
            self.assertEqual(schema_version[0], SCHEMA_VERSION)
            conn.close()

    def test_entities_validation_page_range_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entities_path = root / "entities.jsonl"
            db_path = root / "store.sqlite"
            self._write_jsonl(
                entities_path,
                [
                    "{invalid json",
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [3.0, "4"],
                        "items": [
                            {
                                "entity": "Alice",
                                "type": "person",
                                "confidence": "0.98",
                                "quote": "Alice said hello.",
                            },
                            {"entity": "   ", "type": "person"},
                        ],
                    },
                    {"file_id": "file_a", "chunk_id": "chunk_1", "items": "not-a-list"},
                ],
            )

            first = build_load_entities(entities_path, db_path, overwrite=True)
            self.assertEqual(first["json_decode_errors"], 1)
            self.assertEqual(first["invalid_record_shape"], 1)
            self.assertEqual(first["invalid_item_shape"], 1)
            self.assertEqual(first["rows_attempted"], 2)
            self.assertEqual(first["rows_inserted"], 1)
            self.assertEqual(first["rows_skipped"], 1)
            self.assertEqual(first["mentions_inserted"], 1)

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT page_start, page_end FROM entities WHERE file_id='file_a' AND chunk_id='chunk_1'"
            ).fetchone()
            self.assertEqual(row, (3, 4))
            conn.close()

            second = build_load_entities(entities_path, db_path, overwrite=False)
            self.assertEqual(second["rows_inserted"], 0)
            self.assertEqual(second["mentions_inserted"], 0)

    def test_load_all_tables_into_single_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entities_path = root / "entities.jsonl"
            events_path = root / "events.jsonl"
            conversations_path = root / "conversations.jsonl"
            db_path = root / "store.sqlite"

            self._write_jsonl(
                entities_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [
                            {
                                "entity": "Alice",
                                "type": "person",
                                "quote": " Alice   said\nhello ",
                                "confidence": 0.8,
                            },
                            {
                                "entity": "Alice",
                                "type": "person",
                                "quote": "Alice said hello",
                                "confidence": 0.8,
                            },
                        ],
                    }
                ],
            )
            self._write_jsonl(
                events_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [{"event": "Meeting", "date": "2024-01-01", "quote": "meeting"}],
                    }
                ],
            )
            self._write_jsonl(
                conversations_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [{"speaker": "Alice", "quote": "hello", "confidence": 0.7}],
                    }
                ],
            )

            build_load_entities(entities_path, db_path, overwrite=True)
            build_load_events(events_path, db_path, overwrite=False)
            build_load_conversations(conversations_path, db_path, overwrite=False)

            conn = sqlite3.connect(db_path)
            self.assertEqual(self._table_count(conn, "entities"), 1)
            self.assertEqual(self._table_count(conn, "mentions"), 1)
            self.assertEqual(self._table_count(conn, "events"), 1)
            self.assertEqual(self._table_count(conn, "conversations"), 1)
            normalized_quote = conn.execute(
                "SELECT quote FROM entities WHERE entity='Alice' AND file_id='file_a' AND chunk_id='chunk_1'"
            ).fetchone()
            self.assertEqual(normalized_quote, ("Alice said hello",))

            mention = conn.execute(
                "SELECT entity, file_id, chunk_id FROM mentions WHERE entity='Alice'"
            ).fetchone()
            self.assertEqual(mention, ("Alice", "file_a", "chunk_1"))
            conn.close()

    def test_loaders_capture_evidence_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entities_path = root / "entities.jsonl"
            events_path = root / "events.jsonl"
            conversations_path = root / "conversations.jsonl"
            signals_path = root / "identity_signals.jsonl"
            db_path = root / "store.sqlite"

            self._write_jsonl(
                entities_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [2, 3],
                        "source_phase": "llm.extract_entities",
                        "extractor_version": "entities:v2:test",
                        "model": "gpt-5-mini",
                        "prompt_hash": "61f7ef8f53b0c8a85d62f6e4a3a13b8f0d2782d7d05a8b8f12947407af7db0ea",
                        "items": [
                            {
                                "entity": "Jane Doe",
                                "type": "person",
                                "quote": "Jane Doe",
                                "char_range": [8, 16],
                                "confidence": 0.9,
                            }
                        ],
                    }
                ],
            )
            self._write_jsonl(
                events_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [2, 3],
                        "items": [
                            {
                                "event": "Approval",
                                "date": "2025-03-05",
                                "quote": "approved",
                                "char_start": 22,
                                "char_end": 30,
                                "source_phase": "llm.extract_events",
                                "extractor_version": "events:v3:test",
                                "model": "gpt-5",
                                "prompt_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                            }
                        ],
                    }
                ],
            )
            self._write_jsonl(
                conversations_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [2, 3],
                        "items": [{"speaker": "Jane", "quote": "hello", "confidence": 0.71}],
                    }
                ],
            )
            self._write_jsonl(
                signals_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [2, 3],
                        "source_phase": "llm.extract_identity_signals",
                        "extractor_version": "identity:v1:test",
                        "model": "gpt-5-mini",
                        "prompt_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                        "items": [
                            {
                                "person": "Jane Doe",
                                "attribute": "case_id",
                                "value": "Case 123",
                                "quote": "Case 123",
                                "confidence": 0.83,
                                "char_start": 40,
                                "char_end": 47,
                            }
                        ],
                    }
                ],
            )

            build_load_entities(entities_path, db_path, overwrite=True)
            build_load_events(events_path, db_path, overwrite=False)
            build_load_conversations(conversations_path, db_path, overwrite=False)
            build_load_identity_signals(signals_path, db_path, overwrite=False)

            conn = sqlite3.connect(db_path)
            entity_row = conn.execute(
                "SELECT source_phase, extractor_version, model, prompt_hash, char_start, char_end "
                "FROM entities"
            ).fetchone()
            self.assertEqual(
                entity_row,
                (
                    "llm.extract_entities",
                    "entities:v2:test",
                    "gpt-5-mini",
                    "61f7ef8f53b0c8a85d62f6e4a3a13b8f0d2782d7d05a8b8f12947407af7db0ea",
                    8,
                    16,
                ),
            )

            event_row = conn.execute(
                "SELECT source_phase, extractor_version, model, prompt_hash, char_start, char_end "
                "FROM events"
            ).fetchone()
            self.assertEqual(
                event_row,
                ("llm.extract_events", "events:v3:test", "gpt-5", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 22, 30),
            )

            conversation_row = conn.execute(
                "SELECT source_phase, extractor_version, model, prompt_hash, char_start, char_end "
                "FROM conversations"
            ).fetchone()
            self.assertEqual(
                conversation_row,
                ("llm.extract_conversations", "llm.extract_conversations:legacy", None, None, None, None),
            )

            signal_row = conn.execute(
                "SELECT source_phase, extractor_version, model, prompt_hash, char_start, char_end "
                "FROM identity_signals"
            ).fetchone()
            self.assertEqual(
                signal_row,
                (
                    "llm.extract_identity_signals",
                    "identity:v1:test",
                    "gpt-5-mini",
                    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    40,
                    47,
                ),
            )
            conn.close()


if __name__ == "__main__":
    unittest.main()
