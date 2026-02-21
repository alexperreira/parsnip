import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from entity_resolution.phase8_resolve_people import build_resolve_people
from knowledge_graph.phase13_publish_graph import build_publish_graph
from loaders.store import ensure_schema


class Phase13PublishGraphTest(unittest.TestCase):
    def test_publish_graph_writes_manifest_and_cypher(self):
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

            build_resolve_people(str(db_path), person_types="person", reset=True)

            result = build_publish_graph(
                str(db_path),
                str(out_dir),
                kg_reset=True,
                compression="none",
                strict=True,
                run_parity_checks=True,
            )
            self.assertIn("export", result)
            self.assertIn("neo4j_cypher", result)
            self.assertEqual(result["parity"]["counts"]["diffs"], 0)

            manifest_path = out_dir / "build_manifest.json"
            self.assertTrue(manifest_path.exists())
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["parity"]["counts"]["diffs"], 0)

            cypher_path = Path(result["neo4j_cypher"]["path"])
            self.assertTrue(cypher_path.exists())
            cypher_text = cypher_path.read_text(encoding="utf-8")
            self.assertIn("CREATE CONSTRAINT person_id", cypher_text)


if __name__ == "__main__":
    unittest.main()

