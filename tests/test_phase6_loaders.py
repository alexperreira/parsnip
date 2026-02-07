import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from loaders.load_conversations import build_load_conversations
from loaders.load_entities import build_load_entities
from loaders.load_events import build_load_events


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

            schema_version = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()
            self.assertIsNotNone(schema_version)
            self.assertEqual(schema_version[0], "1")
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


if __name__ == "__main__":
    unittest.main()
