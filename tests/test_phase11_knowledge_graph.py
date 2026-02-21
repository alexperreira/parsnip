import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from entity_resolution.phase8_resolve_people import build_resolve_people
from file_parser.compress_io import open_text_reader
from knowledge_graph.phase11_materialize_edges import build_materialize_edges
from knowledge_graph.phase11_build_kg import build_export_kg
from loaders.store import ensure_schema


class Phase11KnowledgeGraphTest(unittest.TestCase):
    def test_materialize_edges_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "store.sqlite"

            conn = sqlite3.connect(db_path)
            ensure_schema(conn, overwrite=True)

            conn.execute(
                "INSERT INTO entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Alice", "person", 0.9, "f1", "c1", 1, 1, None),
            )
            cursor = conn.execute(
                "INSERT INTO events(event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Incident", "Jan 2024", 0.8, "f1", "c1", 1, 1, None),
            )
            event_id = int(cursor.lastrowid)

            conn.execute(
                "INSERT INTO identity_signals("
                "person_text, attribute, value, value_norm, confidence, "
                "file_id, chunk_id, page_start, page_end, quote"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("Alice", "case_id", "CASE 123", "CASE123", 0.7, "f1", "c1", 1, 1, "case id"),
            )
            conn.execute(
                "INSERT INTO event_cases(event_id, case_id, case_id_norm, source) VALUES (?, ?, ?, ?)",
                (event_id, "CASE 123", "CASE123", "identity_signals"),
            )
            conn.commit()
            conn.close()

            build_resolve_people(db_path, person_types="person", reset=True)

            first = build_materialize_edges(db_path, reset=True)
            self.assertGreaterEqual(first["edges_inserted"], 1)
            self.assertGreaterEqual(first["evidence_inserted"], 1)

            conn = sqlite3.connect(db_path)
            person_id = conn.execute("SELECT person_id FROM person_clusters").fetchone()[0]

            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM cases WHERE case_id_norm='CASE123'").fetchone()
            )

            edges = conn.execute(
                "SELECT src_type, src_id, edge_type, dst_type, dst_id "
                "FROM kg_edges ORDER BY src_type, src_id, edge_type, dst_type, dst_id"
            ).fetchall()

            self.assertIn(("Event", str(event_id), "IN_CASE", "Case", "CASE123"), edges)
            self.assertIn(("Person", str(person_id), "IN_CASE", "Case", "CASE123"), edges)
            self.assertIn(("Person", str(person_id), "MENTIONED_IN_EVENT", "Event", str(event_id)), edges)

            edge_count = conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]
            evidence_count = conn.execute("SELECT COUNT(*) FROM kg_edge_evidence").fetchone()[0]
            conn.close()

            second = build_materialize_edges(db_path, reset=False)
            self.assertEqual(second["edges_inserted"], 0)
            self.assertEqual(second["evidence_inserted"], 0)

            conn = sqlite3.connect(db_path)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0], edge_count)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM kg_edge_evidence").fetchone()[0], evidence_count
            )
            conn.close()

    def test_export_kg_is_deterministic_and_validates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "store.sqlite"
            out_dir = root / "kg"

            conn = sqlite3.connect(db_path)
            ensure_schema(conn, overwrite=True)

            conn.execute(
                "INSERT INTO entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Alice", "person", 0.9, "f1", "c1", 1, 1, None),
            )
            cursor = conn.execute(
                "INSERT INTO events(event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Incident", "Jan 2024", 0.8, "f1", "c1", 1, 1, None),
            )
            event_id = int(cursor.lastrowid)

            conn.execute(
                "INSERT INTO identity_signals("
                "person_text, attribute, value, value_norm, confidence, "
                "file_id, chunk_id, page_start, page_end, quote"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("Alice", "case_id", "CASE 123", "CASE123", 0.7, "f1", "c1", 1, 1, "case id"),
            )
            conn.execute(
                "INSERT INTO event_cases(event_id, case_id, case_id_norm, source) VALUES (?, ?, ?, ?)",
                (event_id, "CASE 123", "CASE123", "identity_signals"),
            )
            conn.commit()
            conn.close()

            build_resolve_people(db_path, person_types="person", reset=True)
            build_materialize_edges(db_path, reset=True)

            first = build_export_kg(db_path, out_dir, compression="zstd", zstd_level=1, strict=True)
            second = build_export_kg(db_path, out_dir, compression="zstd", zstd_level=1, strict=True)

            self.assertEqual(first["counts"]["issues"], 0)
            self.assertEqual(second["counts"]["issues"], 0)
            self.assertGreater(first["counts"]["edges"], 0)
            self.assertGreater(first["counts"]["edge_evidence"], 0)

            def read_jsonl(path: str):
                records = []
                with open_text_reader(Path(path)) as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        records.append(json.loads(line))
                return records

            person_nodes_1 = read_jsonl(first["nodes"]["person"])
            person_nodes_2 = read_jsonl(second["nodes"]["person"])
            self.assertEqual(person_nodes_1, person_nodes_2)
            self.assertEqual(person_nodes_1[0]["node_type"], "Person")

            edges_1 = read_jsonl(first["edges"]["edges"])
            edges_2 = read_jsonl(second["edges"]["edges"])
            self.assertEqual(edges_1, edges_2)
            # Stable ordering: first edge is deterministic given one person/event/case.
            self.assertEqual(edges_1[0]["src_type"], "Event")

            ev_1 = read_jsonl(first["nodes"]["event"])
            self.assertEqual(ev_1[0]["node_type"], "Event")

            case_1 = read_jsonl(first["nodes"]["case"])
            self.assertEqual(case_1[0]["node_type"], "Case")


if __name__ == "__main__":
    unittest.main()
