import argparse
import json
import re
import time
from pathlib import Path

from loaders.store import (
    as_clean_text,
    as_float,
    canonical_quote,
    connect_db,
    ensure_schema,
    mark_loader_run,
    normalize_page_range,
)


ALLOWED_ATTRIBUTES = {"dob", "address", "case_id"}
_DOB_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DOB_MDY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _parse_args():
    parser = argparse.ArgumentParser(description="Load identity signals JSONL into SQLite.")
    parser.add_argument(
        "--input",
        default="identity_signals.jsonl",
        help="Identity signals JSONL path (default: identity_signals.jsonl).",
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


def _normalize_attribute(value):
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if value in ALLOWED_ATTRIBUTES else None


def _normalize_value(attribute, value):
    cleaned = as_clean_text(value)
    if cleaned is None:
        return None

    if attribute == "case_id":
        # Stable, conservative normalization for join keys.
        return "".join(cleaned.split()).upper()
    if attribute == "address":
        return " ".join(cleaned.split()).lower()
    if attribute == "dob":
        if _DOB_ISO_RE.match(cleaned):
            return cleaned
        mdy = _DOB_MDY_RE.match(cleaned)
        if mdy:
            month = int(mdy.group(1))
            day = int(mdy.group(2))
            year = int(mdy.group(3))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year:04d}-{month:02d}-{day:02d}"
        return cleaned

    return cleaned


def build_load_identity_signals(input_path, db_path, overwrite=False):
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
        "invalid_item_shape": 0,
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
            chunk_id = as_clean_text(record.get("chunk_id"))
            items = record.get("items")
            if not file_id or not chunk_id or not isinstance(items, list):
                summary["invalid_record_shape"] += 1
                continue

            page_start, page_end = normalize_page_range(record.get("page_range"))
            for item in items:
                summary["rows_attempted"] += 1
                if not isinstance(item, dict):
                    summary["invalid_item_shape"] += 1
                    summary["rows_skipped"] += 1
                    continue

                person_text = as_clean_text(item.get("person"))
                attribute = _normalize_attribute(item.get("attribute"))
                value = as_clean_text(item.get("value"))
                quote = canonical_quote(item.get("quote"))
                confidence = as_float(item.get("confidence"))
                if not person_text or not attribute or not value or not quote:
                    summary["invalid_item_shape"] += 1
                    summary["rows_skipped"] += 1
                    continue

                value_norm = _normalize_value(attribute, value)
                row = (
                    person_text,
                    attribute,
                    value,
                    value_norm,
                    confidence,
                    file_id,
                    chunk_id,
                    page_start,
                    page_end,
                    quote,
                )
                result = conn.execute(
                    "INSERT OR IGNORE INTO identity_signals("
                    "person_text, attribute, value, value_norm, confidence, "
                    "file_id, chunk_id, page_start, page_end, quote"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
                if result.rowcount == 1:
                    summary["rows_inserted"] += 1
                else:
                    summary["rows_skipped"] += 1

    mark_loader_run(conn, "identity_signals")
    conn.commit()
    conn.close()
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return summary


def main():
    args = _parse_args()
    summary = build_load_identity_signals(args.input, args.db, overwrite=args.overwrite)
    print("Load identity signals summary")
    print(f"  records_total: {summary['records_total']}")
    print(f"  rows_attempted: {summary['rows_attempted']}")
    print(f"  rows_inserted: {summary['rows_inserted']}")
    print(f"  rows_skipped: {summary['rows_skipped']}")
    print(f"  json_decode_errors: {summary['json_decode_errors']}")
    print(f"  invalid_record_shape: {summary['invalid_record_shape']}")
    print(f"  invalid_item_shape: {summary['invalid_item_shape']}")
    print(f"  elapsed_seconds: {summary['elapsed_seconds']}")


if __name__ == "__main__":
    main()

