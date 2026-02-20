import argparse
import json
import time
from pathlib import Path

from loaders.store import as_clean_text, connect_db, ensure_schema, mark_loader_run


def _parse_args():
    parser = argparse.ArgumentParser(description="Load Phase 0 manifest JSONL into SQLite.")
    parser.add_argument(
        "--input",
        default="output/manifest.jsonl",
        help="Manifest JSONL path (default: output/manifest.jsonl).",
    )
    parser.add_argument(
        "--db",
        default="output/store.sqlite",
        help="SQLite DB path (default: output/store.sqlite).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Drop and recreate all store tables before loading.",
    )
    return parser.parse_args()


def build_load_manifest(input_path, db_path, overwrite=False):
    input_path = Path(input_path)
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")
    conn = connect_db(db_path)
    ensure_schema(conn, overwrite=overwrite)

    summary = {
        "records_total": 0,
        "rows_attempted": 0,
        "rows_inserted": 0,
        "rows_skipped": 0,
        "json_decode_errors": 0,
        "invalid_record_shape": 0,
    }
    started = time.monotonic()

    with input_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            summary["records_total"] += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                summary["json_decode_errors"] += 1
                continue
            if not isinstance(record, dict):
                summary["invalid_record_shape"] += 1
                continue

            file_id = as_clean_text(record.get("file_id"))
            if not file_id:
                summary["invalid_record_shape"] += 1
                continue

            row = (
                file_id,
                as_clean_text(record.get("source_type")),
                as_clean_text(record.get("container_path")),
                as_clean_text(record.get("virtual_path")),
                as_clean_text(record.get("mtime")),
                record.get("size_bytes") if isinstance(record.get("size_bytes"), int) else None,
            )
            summary["rows_attempted"] += 1
            result = conn.execute(
                "INSERT OR IGNORE INTO files("
                "file_id, source_type, container_path, virtual_path, mtime_utc, size_bytes"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                row,
            )
            if result.rowcount == 1:
                summary["rows_inserted"] += 1
            else:
                summary["rows_skipped"] += 1

    mark_loader_run(conn, "manifest")
    conn.commit()
    conn.close()
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return summary


def main():
    args = _parse_args()
    summary = build_load_manifest(args.input, args.db, overwrite=args.overwrite)
    print("Load manifest summary")
    print(f"  records_total: {summary['records_total']}")
    print(f"  rows_attempted: {summary['rows_attempted']}")
    print(f"  rows_inserted: {summary['rows_inserted']}")
    print(f"  rows_skipped: {summary['rows_skipped']}")
    print(f"  json_decode_errors: {summary['json_decode_errors']}")
    print(f"  invalid_record_shape: {summary['invalid_record_shape']}")
    print(f"  elapsed_seconds: {summary['elapsed_seconds']}")


if __name__ == "__main__":
    main()

