import argparse
import json
import sqlite3
import time
from pathlib import Path


def _parse_args():
    parser = argparse.ArgumentParser(description="Load events JSONL into SQLite.")
    parser.add_argument(
        "--input",
        default="events.jsonl",
        help="Events JSONL path (default: events.jsonl).",
    )
    parser.add_argument(
        "--db",
        default="output/store.sqlite",
        help="SQLite DB path (default: output/store.sqlite).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Drop and recreate events table.",
    )
    return parser.parse_args()


def _ensure_schema(conn, overwrite):
    if overwrite:
        conn.execute("DROP TABLE IF EXISTS events")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "event_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "event TEXT,"
        "date TEXT,"
        "confidence REAL,"
        "file_id TEXT,"
        "chunk_id TEXT,"
        "page_start INTEGER,"
        "page_end INTEGER,"
        "quote TEXT"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_file_id ON events(file_id)")
    conn.commit()


def _iter_jsonl(path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield record


def main():
    args = _parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn, args.overwrite)

    inserted = 0
    errors = 0
    started = time.monotonic()

    for record in _iter_jsonl(input_path):
        file_id = record.get("file_id")
        chunk_id = record.get("chunk_id")
        page_range = record.get("page_range") or []
        page_start = page_range[0] if len(page_range) > 0 else None
        page_end = page_range[1] if len(page_range) > 1 else None
        items = record.get("items") or []
        if not isinstance(items, list):
            errors += 1
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            conn.execute(
                "INSERT INTO events(event, date, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.get("event"),
                    item.get("date"),
                    item.get("confidence"),
                    file_id,
                    chunk_id,
                    page_start,
                    page_end,
                    item.get("quote"),
                ),
            )
            inserted += 1
    conn.commit()
    conn.close()

    elapsed = round(time.monotonic() - started, 3)
    print("Load events summary")
    print(f"  inserted: {inserted}")
    print(f"  errors: {errors}")
    print(f"  elapsed_seconds: {elapsed}")


if __name__ == "__main__":
    main()
