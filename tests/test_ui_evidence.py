import tempfile
import unittest
from pathlib import Path

from file_parser.ui_evidence import EvidenceFilter, build_evidence_browser
from loaders.store import connect_db, ensure_schema


class TestUiEvidence(unittest.TestCase):
    def test_build_evidence_browser_filters_pagination_and_sort(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)

            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "Event 1", "2026-01-02", 0.9, "f1", "c1", 1, 1, "event q1"),
            )
            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (2, "Event 2", "2026-01-03", 0.4, "f1", "c2", 2, 2, "event q2"),
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
                (1, "ok", "2026-01-02", "2026-01-02", "day"),
            )
            conn.execute(
                "INSERT INTO event_times(event_id, status, date_start, date_end, precision) VALUES (?, ?, ?, ?, ?)",
                (2, "ok", "2026-01-03", "2026-01-03", "day"),
            )
            conn.execute(
                "INSERT INTO entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Alice", "person", 0.8, "f1", "c1", 1, 1, "alice evidence"),
            )
            conn.execute(
                "INSERT INTO entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Bob", "person", 0.2, "f1", "c2", 2, 2, "bob evidence"),
            )
            conn.execute(
                "INSERT INTO conversations(speaker, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Witness", 0.7, "f1", "c1", 1, 1, "conversation snippet"),
            )
            conn.execute(
                "INSERT INTO identity_signals(person_text, attribute, value, value_norm, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("Alice", "case_id", "CASE-1", "case-1", 0.95, "f1", "c1", 1, 1, "signal q"),
            )
            conn.commit()
            conn.close()

            result = build_evidence_browser(
                str(db_path),
                "case-1",
                page=1,
                page_size=10,
                sort_by="confidence",
                sort_dir="desc",
                filters=EvidenceFilter(source_table="entities", confidence_min=0.5, query="alice"),
            )
            self.assertEqual(result.status, 200)
            self.assertEqual(result.code, "ok")
            self.assertIsNotNone(result.evidence_page)
            self.assertEqual(result.evidence_page.total_rows, 1)
            self.assertEqual(result.evidence_page.total_pages, 1)
            self.assertEqual(len(result.evidence_page.items), 1)
            item = result.evidence_page.items[0]
            self.assertEqual(item.source_table, "entities")
            self.assertEqual(item.chunk_id, "c1")
            self.assertGreaterEqual(item.confidence, 0.5)

    def test_build_evidence_browser_sort_allow_list_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)
            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "Event 1", "2026-01-02", 0.9, "f1", "c1", 1, 1, "event q1"),
            )
            conn.execute(
                "INSERT INTO event_cases(event_id, case_id, case_id_norm, source) VALUES (?, ?, ?, ?)",
                (1, "CASE-1", "case-1", "identity_signals"),
            )
            conn.execute(
                "INSERT INTO entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Alice", "person", 0.8, "f1", "c1", 1, 1, "alice evidence"),
            )
            conn.commit()
            conn.close()

            result = build_evidence_browser(
                str(db_path),
                "case-1",
                page=0,
                page_size=999,
                sort_by="DROP TABLE",
                sort_dir="NOPE",
            )
            self.assertEqual(result.status, 200)
            self.assertEqual(result.evidence_page.page, 1)
            self.assertEqual(result.evidence_page.page_size, 25)
            self.assertEqual(result.evidence_page.sort_by, "page_start")
            self.assertEqual(result.evidence_page.sort_dir, "asc")

    def test_build_evidence_browser_case_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)
            conn.commit()
            conn.close()

            result = build_evidence_browser(str(db_path), "missing-case")
            self.assertEqual(result.status, 404)
            self.assertEqual(result.code, "case_not_found")
            self.assertEqual(len(result.widget_states), 1)
            self.assertEqual(result.widget_states[0].status, "error")


if __name__ == "__main__":
    unittest.main()
