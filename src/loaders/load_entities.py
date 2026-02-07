import argparse
import json
import sqlite3
import time
from pathlib import Path


def _parse_args():
    parser = argparse.ArgumentParser(description="Load entities JSONL into SQLite.")
    parser.add_argument(
        "--input",
        default="entities.jsonl",
        help="Entities JSONL path (default: entities.jsonl).",
    )
    parser.add_argument(
        "--db",
        default="output/store.sqlite",
        help="SQLite DB path (default: output/store.sqlite).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Drop and recreate entity-related tables.",
    )
    return parser.parse_args()


def _ensure_schema(conn, overwrite):
    if overwrite:
        conn.execute("DROP TABLE IF EXISTS mentions")
        conn.execute("DROP TABLE IF EXISTS entities")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entities ("
        "entity_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "entity TEXT,"
        "type TEXT,"
        "confidence REAL,"
        "file_id TEXT,"
        "chunk_id TEXT,"
        "page_start INTEGER,"
        "page_end INTEGER,"
        "quote TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS mentions ("
        "mention_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "entity TEXT,"
        "file_id TEXT,"
        "chunk_id TEXT"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_file_id ON entities(file_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mentions_entity ON mentions(entity)")
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
    mentions = 0
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
                "INSERT INTO entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.get("entity"),
                    item.get("type"),
                    item.get("confidence"),
                    file_id,
                    chunk_id,
                    page_start,
                    page_end,
                    item.get("quote"),
                ),
            )
            inserted += 1
            if item.get("entity"):
                conn.execute(
                    "INSERT INTO mentions(entity, file_id, chunk_id) VALUES (?, ?, ?)",
                    (item.get("entity"), file_id, chunk_id),
                )
                mentions += 1
    conn.commit()
    conn.close()

    elapsed = round(time.monotonic() - started, 3)
    print("Load entities summary")
    print(f"  inserted: {inserted}")
    print(f"  mentions: {mentions}")
    print(f"  errors: {errors}")
    print(f"  elapsed_seconds: {elapsed}")


if __name__ == "__main__":
    main()
