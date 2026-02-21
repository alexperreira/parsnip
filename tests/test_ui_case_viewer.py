import sqlite3
import tempfile
import unittest
from pathlib import Path

from file_parser.ui_case_viewer import build_case_view
from loaders.store import connect_db, ensure_schema


class TestUiCaseViewer(unittest.TestCase):
    def test_build_case_view_summary_counts_and_entities(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)

            conn.execute(
                "INSERT INTO cases(case_id_norm, case_id_display, sources_json) VALUES (?, ?, ?)",
                ("case-1", "Case One", "[]"),
            )
            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "Incident A", "2026-01-01", 0.8, "f1", "c1", 1, 1, "event quote a"),
            )
            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (2, "Incident B", "unclear", 0.6, "f1", "c2", 2, 2, "event quote b"),
            )
            conn.execute(
                "INSERT INTO event_cases(event_id, case_id, case_id_norm, source) VALUES (?, ?, ?, ?)",
                (1, "CASE-1", "case-1", "identity_signals"),
            )
            conn.execute(
                "INSERT INTO event_cases(event_id, case_id, case_id_norm, source) VALUES (?, ?, ?, ?)",
                (2, "CASE-1", "case-1", "identity_signals"),
            )
            conn.execute(
                "INSERT INTO event_times(event_id, status, date_start, date_end, precision) VALUES (?, ?, ?, ?, ?)",
                (1, "ok", "2026-01-01", "2026-01-01", "day"),
            )
            conn.execute(
                "INSERT INTO event_times(event_id, status, date_start, date_end, precision) VALUES (?, ?, ?, ?, ?)",
                (2, "invalid_format", None, None, "unknown"),
            )

            conn.execute(
                "INSERT INTO entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Alice Carter", "person", 0.9, "f1", "c1", 1, 1, "alice q"),
            )
            conn.execute(
                "INSERT INTO entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Alice Carter", "person", 0.7, "f1", "c2", 2, 2, "alice q2"),
            )
            conn.execute(
                "INSERT INTO entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Acme Corp", "org", 0.8, "f1", "c1", 1, 1, "acme q"),
            )
            conn.execute(
                "INSERT INTO conversations(speaker, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Witness", 0.6, "f1", "c1", 1, 1, "conv q"),
            )
            conn.execute(
                "INSERT INTO identity_signals(person_text, attribute, value, value_norm, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("Alice Carter", "case_id", "CASE-1", "case-1", 0.9, "f1", "c1", 1, 1, "sig q"),
            )
            conn.commit()
            conn.close()

            result = build_case_view(str(db_path), "case-1", entity_limit=10, evidence_limit=20)

            self.assertEqual(result.status, 200)
            self.assertEqual(result.code, "ok")
            self.assertEqual(result.case_id_display, "Case One")
            self.assertIsNotNone(result.summary)
            self.assertEqual(result.summary.event_count, 2)
            self.assertEqual(result.summary.unresolved_date_count, 1)
            self.assertEqual(result.summary.people_count, 1)
            self.assertEqual(result.summary.evidence_count, 7)
            self.assertEqual(result.linked_entities[0].entity, "Alice Carter")
            self.assertEqual(result.linked_entities[0].occurrences, 2)
            self.assertTrue(len(result.evidence) > 0)
            self.assertEqual([w.widget_id for w in result.widget_states], ["summary", "linked_entities", "recent_evidence"])
            self.assertTrue(all(w.status in {"ready", "empty"} for w in result.widget_states))

    def test_build_case_view_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)
            conn.commit()
            conn.close()

            result = build_case_view(str(db_path), "missing-case")
            self.assertEqual(result.status, 404)
            self.assertEqual(result.code, "case_not_found")
            self.assertIsNone(result.summary)
            self.assertEqual(len(result.widget_states), 1)
            self.assertEqual(result.widget_states[0].status, "error")

    def test_build_case_view_fail_soft_missing_entities_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)
            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "Incident A", "2026-01-01", 0.8, "f1", "c1", 1, 1, "event quote a"),
            )
            conn.execute(
                "INSERT INTO event_cases(event_id, case_id, case_id_norm, source) VALUES (?, ?, ?, ?)",
                (1, "CASE-1", "case-1", "identity_signals"),
            )
            conn.execute(
                "INSERT INTO event_times(event_id, status, date_start, date_end, precision) VALUES (?, ?, ?, ?, ?)",
                (1, "ok", "2026-01-01", "2026-01-01", "day"),
            )
            conn.execute("DROP TABLE entities")
            conn.commit()
            conn.close()

            result = build_case_view(str(db_path), "case-1")
            self.assertEqual(result.status, 200)
            self.assertIsNotNone(result.summary)
            states = {state.widget_id: state.status for state in result.widget_states}
            self.assertEqual(states["summary"], "ready")
            self.assertEqual(states["linked_entities"], "error")
            self.assertEqual(states["recent_evidence"], "error")


if __name__ == "__main__":
    unittest.main()
