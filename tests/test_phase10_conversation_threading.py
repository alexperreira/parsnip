import sqlite3
import tempfile
import unittest
from pathlib import Path

from conversation_threading.phase10_thread_conversations import build_thread_conversations
from conversation_threading.phase10_thread_conversations import _normalize_text, _tokenize
from loaders.store import connect_db, ensure_schema


class Phase10ConversationThreadingTest(unittest.TestCase):
    def test_normalization_and_tokenize_deterministic(self):
        self.assertEqual(_normalize_text("  Alice  "), "alice")
        self.assertEqual(_normalize_text("ALICE!!!"), "alice")
        self.assertEqual(_normalize_text(""), None)

        text = "The quick, brown fox jumps over the lazy dog. And the dog said hello."
        first = _tokenize(text)
        second = _tokenize(text)
        self.assertEqual(first, second)
        self.assertNotIn("the", first)
        self.assertIn("quick", first)

    def test_threads_segments_and_participants(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)

            # Make speaker normalization resolvable to a person_id.
            conn.execute(
                "INSERT INTO person_clusters(person_id, display_name, display_name_norm, dob) "
                "VALUES (1, 'Alice', 'alice', NULL)"
            )

            # Two segments in the same case, same participant.
            conn.execute(
                "INSERT INTO identity_signals("
                "person_text, attribute, value, value_norm, confidence, file_id, chunk_id, page_start, page_end, quote"
                ") VALUES (?, 'case_id', ?, ?, 1.0, ?, ?, 1, 1, '')",
                ("system", "CASE-123", "case-123", "file_a", "chunk_1"),
            )
            conn.execute(
                "INSERT INTO identity_signals("
                "person_text, attribute, value, value_norm, confidence, file_id, chunk_id, page_start, page_end, quote"
                ") VALUES (?, 'case_id', ?, ?, 1.0, ?, ?, 1, 1, '')",
                ("system", "CASE-123", "case-123", "file_b", "chunk_2"),
            )

            conn.execute(
                "INSERT INTO conversations(speaker, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, 0.9, ?, ?, 1, 1, ?)",
                ("Alice", "file_a", "chunk_1", "We should schedule the meeting tomorrow."),
            )
            conn.execute(
                "INSERT INTO conversations(speaker, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, 0.9, ?, ?, 1, 1, ?)",
                ("Alice", "file_b", "chunk_2", "Meeting is confirmed. Call me after lunch."),
            )
            conn.commit()
            conn.close()

            result = build_thread_conversations(db_path=str(db_path), reset=True)
            self.assertGreaterEqual(result["summary"]["segments_built"], 2)

            conn = sqlite3.connect(db_path)
            threads = conn.execute("SELECT thread_id, label, case_id_norm FROM conversation_threads").fetchall()
            self.assertEqual(len(threads), 1)
            self.assertEqual(threads[0][2], "case-123")
            self.assertTrue(isinstance(threads[0][1], str) and threads[0][1].startswith("Conversation:"))

            membership = conn.execute(
                "SELECT COUNT(*) FROM conversation_thread_segments"
            ).fetchone()[0]
            self.assertEqual(membership, 2)

            participants = conn.execute(
                "SELECT participant_key, person_id FROM conversation_thread_participants"
            ).fetchall()
            self.assertEqual(participants, [("p:1", 1)])
            conn.close()

    def test_candidate_key_fanout_cap_prevents_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)

            # Same case_id across multiple segments.
            for file_id, chunk_id in [("a", "c1"), ("b", "c2"), ("c", "c3")]:
                conn.execute(
                    "INSERT INTO identity_signals("
                    "person_text, attribute, value, value_norm, confidence, file_id, chunk_id, page_start, page_end, quote"
                    ") VALUES (?, 'case_id', ?, ?, 1.0, ?, ?, 1, 1, '')",
                    ("system", "CASE-1", "case-1", file_id, chunk_id),
                )
                conn.execute(
                    "INSERT INTO conversations(speaker, confidence, file_id, chunk_id, page_start, page_end, quote) "
                    "VALUES ('Alice', 0.9, ?, ?, 1, 1, 'Meeting meeting meeting')",
                    (file_id, chunk_id),
                )
            conn.commit()
            conn.close()

            # With a very small fanout cap, shared keys get ignored -> no edges, no linking.
            build_thread_conversations(db_path=str(db_path), reset=True, max_key_fanout=1)

            conn = sqlite3.connect(db_path)
            edges = conn.execute("SELECT COUNT(*) FROM conversation_thread_edges").fetchone()[0]
            threads = conn.execute("SELECT COUNT(*) FROM conversation_threads").fetchone()[0]
            memberships = conn.execute("SELECT COUNT(*) FROM conversation_thread_segments").fetchone()[0]
            conn.close()

            self.assertEqual(edges, 0)
            self.assertEqual(threads, 3)
            self.assertEqual(memberships, 3)

    def test_clustering_deterministic_three_segments_one_thread(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)
            conn.execute(
                "INSERT INTO person_clusters(person_id, display_name, display_name_norm, dob) "
                "VALUES (1, 'Alice', 'alice', NULL)"
            )
            for file_id, chunk_id, quote in [
                ("a", "c1", "Project update meeting notes"),
                ("b", "c2", "Meeting follow up action items"),
                ("c", "c3", "Schedule meeting next week"),
            ]:
                conn.execute(
                    "INSERT INTO identity_signals("
                    "person_text, attribute, value, value_norm, confidence, file_id, chunk_id, page_start, page_end, quote"
                    ") VALUES (?, 'case_id', ?, ?, 1.0, ?, ?, 1, 1, '')",
                    ("system", "CASE-2", "case-2", file_id, chunk_id),
                )
                conn.execute(
                    "INSERT INTO conversations(speaker, confidence, file_id, chunk_id, page_start, page_end, quote) "
                    "VALUES ('Alice', 0.9, ?, ?, 1, 1, ?)",
                    (file_id, chunk_id, quote),
                )
            conn.commit()
            conn.close()

            build_thread_conversations(db_path=str(db_path), reset=True)
            conn = sqlite3.connect(db_path)
            threads = conn.execute("SELECT thread_key FROM conversation_threads WHERE case_id_norm='case-2'").fetchall()
            memberships = conn.execute(
                "SELECT COUNT(*) FROM conversation_thread_segments"
            ).fetchone()[0]
            conn.close()

            self.assertEqual(len(threads), 1)
            self.assertEqual(memberships, 3)

    def test_rerun_threading_idempotent_by_thread_key_and_membership(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "store.sqlite"
            conn = connect_db(db_path)
            ensure_schema(conn, overwrite=True)
            for file_id, chunk_id, quote in [("a", "c1", "alpha beta gamma"), ("b", "c2", "alpha beta")]:
                conn.execute(
                    "INSERT INTO identity_signals("
                    "person_text, attribute, value, value_norm, confidence, file_id, chunk_id, page_start, page_end, quote"
                    ") VALUES (?, 'case_id', ?, ?, 1.0, ?, ?, 1, 1, '')",
                    ("system", "CASE-3", "case-3", file_id, chunk_id),
                )
                conn.execute(
                    "INSERT INTO conversations(speaker, confidence, file_id, chunk_id, page_start, page_end, quote) "
                    "VALUES ('Bob', 0.9, ?, ?, 1, 1, ?)",
                    (file_id, chunk_id, quote),
                )
            conn.commit()
            conn.close()

            def snapshot():
                conn2 = sqlite3.connect(db_path)
                rows = conn2.execute(
                    "SELECT t.thread_key, s.file_id, s.chunk_id "
                    "FROM conversation_threads t "
                    "JOIN conversation_thread_segments ts ON ts.thread_id = t.thread_id "
                    "JOIN conversation_segments s ON s.segment_id = ts.segment_id "
                    "ORDER BY t.thread_key, s.file_id, s.chunk_id"
                ).fetchall()
                conn2.close()
                mapping = {}
                for thread_key, file_id, chunk_id in rows:
                    mapping.setdefault(thread_key, []).append(f"{file_id}:{chunk_id}")
                return mapping

            build_thread_conversations(db_path=str(db_path), reset=True)
            first = snapshot()
            build_thread_conversations(db_path=str(db_path), reset=True)
            second = snapshot()
            self.assertEqual(first, second)
