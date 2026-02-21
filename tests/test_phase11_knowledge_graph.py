import sqlite3
import tempfile
import unittest
from pathlib import Path

from entity_resolution.phase8_resolve_people import build_resolve_people
from knowledge_graph.phase11_materialize_edges import build_materialize_edges
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


if __name__ == "__main__":
    unittest.main()

