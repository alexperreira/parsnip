import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from loaders.store import ensure_schema
from timeline.phase9_stitch_timeline import build_stitch_timeline


class Phase9TimelineStitchTest(unittest.TestCase):
    def _insert_event(self, conn, event, date_raw, file_id, chunk_id):
        cursor = conn.execute(
            "INSERT INTO events(event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event, date_raw, 0.9, file_id, chunk_id, 1, 1, None),
        )
        return int(cursor.lastrowid)

    def _insert_case_signal(self, conn, file_id, chunk_id, value, value_norm):
        conn.execute(
            "INSERT INTO identity_signals("
            "person_text, attribute, value, value_norm, confidence, "
            "file_id, chunk_id, page_start, page_end, quote"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("Someone", "case_id", value, value_norm, 0.9, file_id, chunk_id, 1, 1, "case id"),
        )

    def test_chunk_anchor_relative_resolution_and_case_links(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "store.sqlite"
            conn = sqlite3.connect(db_path)
            ensure_schema(conn, overwrite=True)

            event_abs = self._insert_event(conn, "E1", "Jan 2024", "f1", "c1")
            event_rel = self._insert_event(conn, "E2", "last Tuesday", "f1", "c2")
            event_amb = self._insert_event(conn, "E3", "2024-01-05 and 2024-01-06", "f2", "c3")

            self._insert_case_signal(conn, "f1", "c1", "CASE 123", "CASE123")
            self._insert_case_signal(conn, "f1", "c2", "CASE 123", "CASE123")
            conn.commit()
            conn.close()

            chunks_path = root / "chunks.jsonl"
            chunks_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "file_id": "f1",
                                "chunk_id": "c2",
                                "text": "As of 2026-02-20, this happened.",
                            }
                        ),
                        json.dumps({"file_id": "f1", "chunk_id": "c1", "text": "No anchor needed."}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            summary = build_stitch_timeline(db_path, chunks_path=chunks_path, reset=True)
            self.assertEqual(summary["events_total"], 3)

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT status, date_start, date_end, anchor_date FROM event_times WHERE event_id=?",
                (event_rel,),
            ).fetchone()
            self.assertEqual(row, ("ok", "2026-02-17", "2026-02-17", "2026-02-20"))

            amb = conn.execute(
                "SELECT status FROM event_times WHERE event_id=?",
                (event_amb,),
            ).fetchone()
            self.assertEqual(amb[0], "unresolved_ambiguous")

            cases = conn.execute(
                "SELECT case_id_norm, source FROM event_cases WHERE event_id=? ORDER BY case_id_norm",
                (event_abs,),
            ).fetchall()
            self.assertEqual(cases, [("CASE123", "identity_signals")])

            fallback = conn.execute(
                "SELECT case_id_norm, source FROM event_cases WHERE event_id=?",
                (event_amb,),
            ).fetchone()
            self.assertEqual(fallback, ("file:f2", "fallback"))
            conn.close()

    def test_manifest_anchor_relative_resolution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "store.sqlite"
            conn = sqlite3.connect(db_path)
            ensure_schema(conn, overwrite=True)
            event_rel = self._insert_event(conn, "E4", "tomorrow", "f3", "c4")
            conn.commit()
            conn.close()

            manifest_path = root / "manifest.jsonl"
            manifest_path.write_text(
                json.dumps(
                    {
                        "file_id": "f3",
                        "source_type": "fs",
                        "container_path": None,
                        "virtual_path": "x.pdf",
                        "size_bytes": 1,
                        "mtime": "2026-02-20T00:00:00+00:00",
                        "ext": "pdf",
                    },
                    ensure_ascii=True,
                )
                + "\n",
                encoding="utf-8",
            )

            summary = build_stitch_timeline(db_path, manifest_path=manifest_path, reset=True)
            self.assertEqual(summary["events_total"], 1)

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT status, date_start, anchor_date FROM event_times WHERE event_id=?",
                (event_rel,),
            ).fetchone()
            self.assertEqual(row, ("ok", "2026-02-21", "2026-02-20"))
            conn.close()


if __name__ == "__main__":
    unittest.main()

