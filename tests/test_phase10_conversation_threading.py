import sqlite3
import tempfile
import unittest
from pathlib import Path

from conversation_threading.phase10_thread_conversations import build_thread_conversations
from loaders.store import connect_db, ensure_schema


class Phase10ConversationThreadingTest(unittest.TestCase):
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

