import tempfile
import unittest
from pathlib import Path

from file_parser.ui_indicators import build_case_indicators
from loaders.store import connect_db, ensure_schema


class TestUiIndicators(unittest.TestCase):
    def test_build_case_indicators_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)

            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "Event A", "2026-01-01", 0.8, "f1", "c1", 1, 1, "q1"),
            )
            conn.execute(
                "INSERT INTO event_cases(event_id, case_id, case_id_norm, source) VALUES (?, ?, ?, ?)",
                (1, "CASE-1", "case-1", "identity_signals"),
            )

            conn.execute(
                "INSERT INTO person_clusters(person_id, display_name, display_name_norm, dob) VALUES (?, ?, ?, ?)",
                (1, "Alice", "alice", None),
            )
            conn.execute(
                "INSERT INTO person_observations(obs_id, name, name_norm, file_id, chunk_id, page_start, page_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (10, "Alice", "alice", "f1", "c1", 1, 1),
            )
            conn.execute(
                "INSERT INTO person_observations(obs_id, name, name_norm, file_id, chunk_id, page_start, page_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (11, "Alicia", "alicia", "f1", "c1", 1, 1),
            )
            conn.execute("INSERT INTO person_cluster_members(person_id, obs_id) VALUES (?, ?)", (1, 10))
            conn.execute("INSERT INTO person_cluster_members(person_id, obs_id) VALUES (?, ?)", (1, 11))
            conn.execute(
                "INSERT INTO person_resolution_edges(left_obs_id, right_obs_id, decision, score, reasons_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (10, 11, "needs_review", 5.0, "[\"weak_match\"]"),
            )

            conn.execute(
                "INSERT INTO conversation_threads(thread_id, case_id_norm, thread_key, label, label_method, created_utc) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (1, "case-1", "k1", "Thread 1", "rules", "2026-02-21T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO conversation_segments(segment_id, file_id, chunk_id, page_start, page_end, case_id_norm, case_source, anchor_date, utterance_count, participants_json, features_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "f1", "c1", 1, 1, "case-1", "identity_signals", "2026-01-01", 2, "[]", "{}"),
            )
            conn.execute(
                "INSERT INTO conversation_thread_segments(thread_id, segment_id, sort_key) VALUES (?, ?, ?)",
                (1, 1, "2026-01-01|1"),
            )
            conn.execute(
                "INSERT INTO conversation_thread_participants(thread_id, participant_key, person_id, speaker_norm, source) VALUES (?, ?, ?, ?, ?)",
                (1, "alice", 1, "alice", "rules"),
            )
            conn.commit()
            conn.close()

            result = build_case_indicators(str(db_path), "case-1")
            self.assertEqual(result.status, 200)
            self.assertEqual(result.code, "ok")
            self.assertIsNotNone(result.dedupe)
            self.assertEqual(result.dedupe.people_clusters, 1)
            self.assertEqual(result.dedupe.merge_needs_review, 1)
            self.assertIsNotNone(result.threading)
            self.assertEqual(result.threading.threads_total, 1)
            self.assertEqual(result.threading.segments_total, 1)
            self.assertEqual(result.threading.participants_total, 1)
            self.assertEqual(result.threading.top_thread_ids, [1])

    def test_build_case_indicators_case_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)
            conn.commit()
            conn.close()

            result = build_case_indicators(str(db_path), "missing-case")
            self.assertEqual(result.status, 404)
            self.assertEqual(result.code, "case_not_found")
            self.assertEqual(len(result.widget_states), 1)
            self.assertEqual(result.widget_states[0].status, "error")

    def test_build_case_indicators_fail_soft_missing_indicator_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)
            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "Event A", "2026-01-01", 0.8, "f1", "c1", 1, 1, "q1"),
            )
            conn.execute(
                "INSERT INTO event_cases(event_id, case_id, case_id_norm, source) VALUES (?, ?, ?, ?)",
                (1, "CASE-1", "case-1", "identity_signals"),
            )
            conn.execute("DROP TABLE person_resolution_edges")
            conn.execute("DROP TABLE conversation_threads")
            conn.commit()
            conn.close()

            result = build_case_indicators(str(db_path), "case-1")
            self.assertEqual(result.status, 200)
            self.assertIsNone(result.dedupe)
            self.assertIsNone(result.threading)
            states = {state.widget_id: state.status for state in result.widget_states}
            self.assertEqual(states["dedupe_indicators"], "error")
            self.assertEqual(states["thread_indicators"], "error")


if __name__ == "__main__":
    unittest.main()
