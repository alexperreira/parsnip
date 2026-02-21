import tempfile
import unittest
from pathlib import Path

from file_parser.ui_timeline import build_case_timeline
from file_parser.ui_shell import SharedFilterState
from loaders.store import connect_db, ensure_schema


class TestUiTimeline(unittest.TestCase):
    def test_build_case_timeline_orders_normalized_and_unresolved(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)

            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "Event Late", "2026-01-05", 0.8, "f1", "c1", 1, 1, "q1"),
            )
            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (2, "Event Early", "2026-01-01", 0.7, "f1", "c2", 2, 2, "q2"),
            )
            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (3, "Event Unknown", "last tuesday", 0.6, "f1", "c3", 3, 3, "q3"),
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
                "INSERT INTO event_cases(event_id, case_id, case_id_norm, source) VALUES (?, ?, ?, ?)",
                (3, "CASE-1", "case-1", "identity_signals"),
            )
            conn.execute(
                "INSERT INTO event_times(event_id, status, date_start, date_end, precision, parser) VALUES (?, ?, ?, ?, ?, ?)",
                (1, "ok", "2026-01-05", "2026-01-05", "day", "absolute_v1"),
            )
            conn.execute(
                "INSERT INTO event_times(event_id, status, date_start, date_end, precision, parser) VALUES (?, ?, ?, ?, ?, ?)",
                (2, "ok", "2026-01-01", "2026-01-01", "day", "absolute_v1"),
            )
            conn.execute(
                "INSERT INTO event_times(event_id, status, date_start, date_end, precision, parser) VALUES (?, ?, ?, ?, ?, ?)",
                (3, "unresolved_relative", None, None, "unknown", "relative_v1"),
            )
            conn.commit()
            conn.close()

            result = build_case_timeline(str(db_path), "case-1")
            self.assertEqual(result.status, 200)
            self.assertEqual(result.code, "ok")
            self.assertEqual([row.event_id for row in result.normalized_rows], [2, 1])
            self.assertEqual([row.event_id for row in result.unresolved_rows], [3])
            self.assertEqual(result.unresolved_rows[0].status, "unresolved_relative")
            self.assertEqual(result.unresolved_rows[0].provenance.source_table, "events")

            filtered = build_case_timeline(
                str(db_path),
                "ignored-case-id",
                shared_filters=SharedFilterState(
                    case_id_norm="case-1",
                    date_start="2026-01-02",
                    confidence_min=0.75,
                ),
            )
            self.assertEqual([row.event_id for row in filtered.normalized_rows], [1])
            self.assertEqual(len(filtered.unresolved_rows), 0)

    def test_build_case_timeline_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)
            conn.commit()
            conn.close()

            result = build_case_timeline(str(db_path), "missing-case")
            self.assertEqual(result.status, 404)
            self.assertEqual(result.code, "case_not_found")
            self.assertEqual(len(result.widget_states), 1)
            self.assertEqual(result.widget_states[0].status, "error")

    def test_build_case_timeline_fail_soft_without_event_times(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)
            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "Event A", "unknown", 0.5, "f1", "c1", 1, 1, "q1"),
            )
            conn.execute(
                "INSERT INTO event_cases(event_id, case_id, case_id_norm, source) VALUES (?, ?, ?, ?)",
                (1, "CASE-1", "case-1", "identity_signals"),
            )
            conn.execute("DROP TABLE event_times")
            conn.commit()
            conn.close()

            result = build_case_timeline(str(db_path), "case-1")
            self.assertEqual(result.status, 200)
            self.assertEqual(len(result.normalized_rows), 0)
            self.assertEqual(len(result.unresolved_rows), 1)
            self.assertEqual(result.unresolved_rows[0].status, "missing")
            states = {state.widget_id: state.status for state in result.widget_states}
            self.assertEqual(states["normalized_lane"], "empty")
            self.assertEqual(states["unresolved_lane"], "ready")
            self.assertEqual(states["source_drilldown"], "ready")


if __name__ == "__main__":
    unittest.main()
