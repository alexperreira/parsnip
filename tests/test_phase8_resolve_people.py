import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from entity_resolution.phase8_resolve_people import build_resolve_people
from loaders.load_entities import build_load_entities
from loaders.load_identity_signals import build_load_identity_signals


class Phase8ResolvePeopleTest(unittest.TestCase):
    def _write_jsonl(self, path, records):
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def _obs_id(self, conn, name_norm):
        row = conn.execute(
            "SELECT obs_id FROM person_observations WHERE name_norm=? ORDER BY obs_id LIMIT 1",
            (name_norm,),
        ).fetchone()
        self.assertIsNotNone(row)
        return row[0]

    def _edge_decision(self, conn, left_obs_id, right_obs_id):
        a, b = sorted((left_obs_id, right_obs_id))
        row = conn.execute(
            "SELECT decision FROM person_resolution_edges WHERE left_obs_id=? AND right_obs_id=?",
            (a, b),
        ).fetchone()
        self.assertIsNotNone(row)
        return row[0]

    def test_alias_shared_dob_auto_merge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entities_path = root / "entities.jsonl"
            signals_path = root / "identity_signals.jsonl"
            db_path = root / "store.sqlite"

            self._write_jsonl(
                entities_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [{"entity": "Robert Smith", "type": "person", "confidence": 0.9}],
                    },
                    {
                        "file_id": "file_b",
                        "chunk_id": "chunk_9",
                        "page_range": [1, 1],
                        "items": [{"entity": "Bob Smith", "type": "person", "confidence": 0.9}],
                    },
                ],
            )
            build_load_entities(entities_path, db_path, overwrite=True)

            self._write_jsonl(
                signals_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [
                            {
                                "person": "Robert Smith",
                                "attribute": "dob",
                                "value": "1980-01-02",
                                "quote": "Robert Smith (DOB 1980-01-02)",
                                "confidence": 0.9,
                            }
                        ],
                    },
                    {
                        "file_id": "file_b",
                        "chunk_id": "chunk_9",
                        "page_range": [1, 1],
                        "items": [
                            {
                                "person": "Bob Smith",
                                "attribute": "dob",
                                "value": "1980-01-02",
                                "quote": "Bob Smith (DOB 1980-01-02)",
                                "confidence": 0.9,
                            }
                        ],
                    },
                ],
            )
            build_load_identity_signals(signals_path, db_path, overwrite=False)

            summary = build_resolve_people(db_path, person_types="person", reset=True)
            self.assertEqual(summary["clusters_total"], 1)

            conn = sqlite3.connect(db_path)
            robert_id = self._obs_id(conn, "robert smith")
            bob_id = self._obs_id(conn, "bob smith")
            self.assertEqual(self._edge_decision(conn, robert_id, bob_id), "auto_merge")

            member_count = conn.execute(
                "SELECT COUNT(*) FROM person_cluster_members"
            ).fetchone()[0]
            self.assertEqual(member_count, 2)
            conn.close()

    def test_shared_case_id_only_needs_review(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entities_path = root / "entities.jsonl"
            signals_path = root / "identity_signals.jsonl"
            db_path = root / "store.sqlite"

            self._write_jsonl(
                entities_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [{"entity": "Alice Jones", "type": "person", "confidence": 0.9}],
                    },
                    {
                        "file_id": "file_b",
                        "chunk_id": "chunk_2",
                        "page_range": [1, 1],
                        "items": [{"entity": "Bob Smith", "type": "person", "confidence": 0.9}],
                    },
                ],
            )
            build_load_entities(entities_path, db_path, overwrite=True)

            self._write_jsonl(
                signals_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [
                            {
                                "person": "Alice Jones",
                                "attribute": "case_id",
                                "value": "case 123",
                                "quote": "Case ID: case 123",
                                "confidence": 0.8,
                            }
                        ],
                    },
                    {
                        "file_id": "file_b",
                        "chunk_id": "chunk_2",
                        "page_range": [1, 1],
                        "items": [
                            {
                                "person": "Bob Smith",
                                "attribute": "case_id",
                                "value": "CASE123",
                                "quote": "Case ID: CASE123",
                                "confidence": 0.8,
                            }
                        ],
                    },
                ],
            )
            build_load_identity_signals(signals_path, db_path, overwrite=False)

            build_resolve_people(db_path, person_types="person", reset=True)
            conn = sqlite3.connect(db_path)
            alice_id = self._obs_id(conn, "alice jones")
            bob_id = self._obs_id(conn, "bob smith")
            self.assertEqual(self._edge_decision(conn, alice_id, bob_id), "needs_review")
            conn.close()

    def test_same_name_conflicting_dob_no_merge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entities_path = root / "entities.jsonl"
            signals_path = root / "identity_signals.jsonl"
            db_path = root / "store.sqlite"

            self._write_jsonl(
                entities_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [{"entity": "Alice Smith", "type": "person", "confidence": 0.9}],
                    },
                    {
                        "file_id": "file_b",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [{"entity": "Alice Smith", "type": "person", "confidence": 0.9}],
                    },
                ],
            )
            build_load_entities(entities_path, db_path, overwrite=True)

            self._write_jsonl(
                signals_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [
                            {
                                "person": "Alice Smith",
                                "attribute": "dob",
                                "value": "1990-01-01",
                                "quote": "DOB 1990-01-01",
                                "confidence": 0.9,
                            }
                        ],
                    },
                    {
                        "file_id": "file_b",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [
                            {
                                "person": "Alice Smith",
                                "attribute": "dob",
                                "value": "1991-01-01",
                                "quote": "DOB 1991-01-01",
                                "confidence": 0.9,
                            }
                        ],
                    },
                ],
            )
            build_load_identity_signals(signals_path, db_path, overwrite=False)

            build_resolve_people(db_path, person_types="person", reset=True)
            conn = sqlite3.connect(db_path)
            alice_id = self._obs_id(conn, "alice smith")
            # There are two observations with the same name_norm; pick the second by ordering desc.
            alice_id_2 = conn.execute(
                "SELECT obs_id FROM person_observations WHERE name_norm=? ORDER BY obs_id DESC LIMIT 1",
                ("alice smith",),
            ).fetchone()[0]
            self.assertNotEqual(alice_id, alice_id_2)
            self.assertEqual(self._edge_decision(conn, alice_id, alice_id_2), "no_merge")
            conn.close()

    def test_address_and_name_auto_merge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            entities_path = root / "entities.jsonl"
            signals_path = root / "identity_signals.jsonl"
            db_path = root / "store.sqlite"

            self._write_jsonl(
                entities_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [{"entity": "Jane Doe", "type": "person", "confidence": 0.9}],
                    },
                    {
                        "file_id": "file_b",
                        "chunk_id": "chunk_2",
                        "page_range": [1, 1],
                        "items": [{"entity": "Jane Doe", "type": "person", "confidence": 0.9}],
                    },
                ],
            )
            build_load_entities(entities_path, db_path, overwrite=True)

            self._write_jsonl(
                signals_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [
                            {
                                "person": "Jane Doe",
                                "attribute": "address",
                                "value": "123 Main St",
                                "quote": "123 Main St",
                                "confidence": 0.9,
                            }
                        ],
                    },
                    {
                        "file_id": "file_b",
                        "chunk_id": "chunk_2",
                        "page_range": [1, 1],
                        "items": [
                            {
                                "person": "Jane Doe",
                                "attribute": "address",
                                "value": "123 MAIN ST",
                                "quote": "123 MAIN ST",
                                "confidence": 0.9,
                            }
                        ],
                    },
                ],
            )
            build_load_identity_signals(signals_path, db_path, overwrite=False)

            build_resolve_people(db_path, person_types="person", reset=True)
            conn = sqlite3.connect(db_path)
            jane_id = self._obs_id(conn, "jane doe")
            jane_id_2 = conn.execute(
                "SELECT obs_id FROM person_observations WHERE name_norm=? ORDER BY obs_id DESC LIMIT 1",
                ("jane doe",),
            ).fetchone()[0]
            self.assertNotEqual(jane_id, jane_id_2)
            self.assertEqual(self._edge_decision(conn, jane_id, jane_id_2), "auto_merge")
            conn.close()

    def test_resolver_persists_meta_summary(self):
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
                        "page_range": [1, 1],
                        "items": [{"entity": "Alice Jones", "type": "person", "confidence": 0.9}],
                    }
                ],
            )
            build_load_entities(entities_path, db_path, overwrite=True)

            build_resolve_people(db_path, person_types="person", reset=True)
            conn = sqlite3.connect(db_path)
            keys = {
                row[0]
                for row in conn.execute("SELECT key FROM meta WHERE key LIKE 'resolver.people.%'").fetchall()
            }
            self.assertIn("resolver.people.last_run_utc", keys)
            self.assertIn("resolver.people.config_json", keys)
            self.assertIn("resolver.people.summary_json", keys)
            conn.close()

    def test_identity_signals_loader_normalization(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            signals_path = root / "identity_signals.jsonl"
            db_path = root / "store.sqlite"

            self._write_jsonl(
                signals_path,
                [
                    {
                        "file_id": "file_a",
                        "chunk_id": "chunk_1",
                        "page_range": [1, 1],
                        "items": [
                            {
                                "person": "Alice Smith",
                                "attribute": "case_id",
                                "value": "case  12 3",
                                "quote": "case  12 3",
                                "confidence": 0.9,
                            },
                            {
                                "person": "Alice Smith",
                                "attribute": "dob",
                                "value": "1/2/1980",
                                "quote": "1/2/1980",
                                "confidence": 0.9,
                            },
                            {
                                "person": "Alice Smith",
                                "attribute": "address",
                                "value": "  123   Main  St ",
                                "quote": "123 Main St",
                                "confidence": 0.9,
                            },
                        ],
                    }
                ],
            )

            summary = build_load_identity_signals(signals_path, db_path, overwrite=True)
            self.assertEqual(summary["rows_inserted"], 3)

            conn = sqlite3.connect(db_path)
            norms = {
                (row[0], row[1])
                for row in conn.execute(
                    "SELECT attribute, value_norm FROM identity_signals ORDER BY attribute, value_norm"
                ).fetchall()
            }
            self.assertIn(("case_id", "CASE123"), norms)
            self.assertIn(("dob", "1980-01-02"), norms)
            self.assertIn(("address", "123 main st"), norms)
            conn.close()
