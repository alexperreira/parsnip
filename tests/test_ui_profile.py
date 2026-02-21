import tempfile
import unittest
from pathlib import Path

from file_parser.ui_profile import build_person_profile
from loaders.store import connect_db, ensure_schema


class TestUiProfile(unittest.TestCase):
    def test_build_person_profile_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)

            conn.execute(
                "INSERT INTO cases(case_id_norm, case_id_display, sources_json) VALUES (?, ?, ?)",
                ("case-1", "Case One", "[]"),
            )
            conn.execute(
                "INSERT INTO person_clusters(person_id, display_name, display_name_norm, dob) VALUES (?, ?, ?, ?)",
                (1, "Robert Smith", "robert smith", "1980-01-01"),
            )
            conn.execute(
                "INSERT INTO person_observations(obs_id, name, name_norm, file_id, chunk_id, page_start, page_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (10, "Bob Smith", "bob smith", "f1", "c1", 1, 1),
            )
            conn.execute(
                "INSERT INTO person_observations(obs_id, name, name_norm, file_id, chunk_id, page_start, page_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (11, "Robert Smith", "robert smith", "f1", "c2", 2, 2),
            )
            conn.execute("INSERT INTO person_cluster_members(person_id, obs_id) VALUES (?, ?)", (1, 10))
            conn.execute("INSERT INTO person_cluster_members(person_id, obs_id) VALUES (?, ?)", (1, 11))
            conn.execute(
                "INSERT INTO person_resolution_edges(left_obs_id, right_obs_id, decision, score, reasons_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (10, 11, "auto_merge", 10.0, "[\"dob_match\"]"),
            )

            conn.execute(
                "INSERT INTO identity_signals(person_text, attribute, value, value_norm, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("Bob Smith", "case_id", "CASE-1", "case-1", 0.9, "f1", "c1", 1, 1, "sig q 1"),
            )
            conn.execute(
                "INSERT INTO identity_signals(person_text, attribute, value, value_norm, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("Robert Smith", "case_id", "CASE-1", "case-1", 0.9, "f1", "c2", 2, 2, "sig q 2"),
            )

            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, "Interview", "2026-01-01", 0.8, "f1", "c1", 1, 1, "event q1"),
            )
            conn.execute(
                "INSERT INTO events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (2, "Follow up", "unknown", 0.7, "f1", "c2", 2, 2, "event q2"),
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
                (2, "missing_anchor", None, None, "unknown"),
            )

            conn.execute(
                "INSERT INTO entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Bob Smith", "person", 0.9, "f1", "c1", 1, 1, "entity q1"),
            )
            conn.execute(
                "INSERT INTO entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Robert Smith", "person", 0.8, "f1", "c2", 2, 2, "entity q2"),
            )
            conn.execute(
                "INSERT INTO conversations(speaker, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("Witness", 0.5, "f1", "c1", 1, 1, "conv q"),
            )

            conn.commit()
            conn.close()

            result = build_person_profile(str(db_path), "case-1", 1, event_limit=10, evidence_limit=20)

            self.assertEqual(result.status, 200)
            self.assertEqual(result.code, "ok")
            self.assertIsNotNone(result.identity)
            self.assertEqual(result.identity.display_name, "Robert Smith")
            self.assertEqual(result.identity.aliases, ["Bob Smith", "Robert Smith"])
            self.assertEqual(result.identity.merge_status.auto_merge, 1)
            self.assertEqual(len(result.linked_events), 2)
            self.assertGreater(len(result.linked_evidence), 0)
            states = {state.widget_id: state.status for state in result.widget_states}
            self.assertEqual(states["identity"], "ready")
            self.assertEqual(states["linked_events"], "ready")
            self.assertEqual(states["linked_evidence"], "ready")

    def test_build_person_profile_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)
            conn.commit()
            conn.close()

            result = build_person_profile(str(db_path), "case-1", 999)
            self.assertEqual(result.status, 404)
            self.assertEqual(result.code, "person_not_found")
            self.assertIsNone(result.identity)
            self.assertEqual(len(result.widget_states), 1)
            self.assertEqual(result.widget_states[0].status, "error")

    def test_build_person_profile_fail_soft_missing_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)
            conn.execute(
                "INSERT INTO person_clusters(person_id, display_name, display_name_norm, dob) VALUES (?, ?, ?, ?)",
                (1, "Alice Doe", "alice doe", None),
            )
            conn.execute(
                "INSERT INTO person_observations(obs_id, name, name_norm, file_id, chunk_id, page_start, page_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (10, "Alice Doe", "alice doe", "f1", "c1", 1, 1),
            )
            conn.execute("INSERT INTO person_cluster_members(person_id, obs_id) VALUES (?, ?)", (1, 10))
            conn.execute(
                "INSERT INTO identity_signals(person_text, attribute, value, value_norm, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("Alice Doe", "case_id", "CASE-1", "case-1", 0.9, "f1", "c1", 1, 1, "sig q"),
            )
            conn.execute(
                "INSERT INTO entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("Alice Doe", "person", 0.8, "f1", "c1", 1, 1, "entity q"),
            )
            conn.execute("DROP TABLE events")
            conn.execute("DROP TABLE event_cases")
            conn.commit()
            conn.close()

            result = build_person_profile(str(db_path), "case-1", 1)
            self.assertEqual(result.status, 200)
            self.assertIsNotNone(result.identity)
            states = {state.widget_id: state.status for state in result.widget_states}
            self.assertEqual(states["identity"], "ready")
            self.assertEqual(states["linked_events"], "error")
            self.assertEqual(states["linked_evidence"], "ready")


if __name__ == "__main__":
    unittest.main()
