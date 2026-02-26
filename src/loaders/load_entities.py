import argparse
import json
import time
from pathlib import Path

from loaders.store import (
    as_clean_text,
    as_float,
    canonical_quote,
    canonical_prompt_hash,
    connect_db,
    ensure_schema,
    mark_loader_run,
    normalize_char_span,
    normalize_extractor_version,
    normalize_page_range,
    normalize_source_phase,
)

DEFAULT_SOURCE_PHASE = "llm.extract_entities"


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
        help="Drop and recreate all store tables before loading.",
    )
    return parser.parse_args()


def build_load_entities(input_path, db_path, overwrite=False):
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
        "mentions_inserted": 0,
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
            source_phase = normalize_source_phase(record.get("source_phase"), DEFAULT_SOURCE_PHASE)
            extractor_version = normalize_extractor_version(
                record.get("extractor_version"),
                f"{source_phase}:legacy",
            )
            model = as_clean_text(record.get("model"))
            prompt_hash = canonical_prompt_hash(record.get("prompt_hash"))

            page_start, page_end = normalize_page_range(record.get("page_range"))
            for item in items:
                summary["rows_attempted"] += 1
                if not isinstance(item, dict):
                    summary["invalid_item_shape"] += 1
                    summary["rows_skipped"] += 1
                    continue
                entity = as_clean_text(item.get("entity"))
                if not entity:
                    summary["invalid_item_shape"] += 1
                    summary["rows_skipped"] += 1
                    continue
                char_start, char_end = normalize_char_span(
                    item.get("char_start"),
                    item.get("char_end"),
                )
                if char_start is None and char_end is None and isinstance(item.get("char_range"), (list, tuple)):
                    char_range = item.get("char_range")
                    char_start, char_end = normalize_char_span(
                        char_range[0] if len(char_range) > 0 else None,
                        char_range[1] if len(char_range) > 1 else None,
                    )
                item_source_phase = normalize_source_phase(item.get("source_phase"), source_phase)
                item_extractor_version = normalize_extractor_version(
                    item.get("extractor_version"),
                    extractor_version,
                )
                item_model = as_clean_text(item.get("model")) or model
                item_prompt_hash = canonical_prompt_hash(item.get("prompt_hash")) or prompt_hash

                row = (
                    entity,
                    as_clean_text(item.get("type")),
                    as_float(item.get("confidence")),
                    file_id,
                    chunk_id,
                    page_start,
                    page_end,
                    canonical_quote(item.get("quote")),
                    char_start,
                    char_end,
                    item_source_phase,
                    item_extractor_version,
                    item_model,
                    item_prompt_hash,
                )
                result = conn.execute(
                    "INSERT OR IGNORE INTO entities("
                    "entity, type, confidence, file_id, chunk_id, page_start, page_end, quote, "
                    "char_start, char_end, source_phase, extractor_version, model, prompt_hash"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )
                if result.rowcount == 1:
                    summary["rows_inserted"] += 1
                else:
                    summary["rows_skipped"] += 1

                mention_result = conn.execute(
                    "INSERT OR IGNORE INTO mentions(entity, file_id, chunk_id) VALUES (?, ?, ?)",
                    (entity, file_id, chunk_id),
                )
                if mention_result.rowcount == 1:
                    summary["mentions_inserted"] += 1

    mark_loader_run(conn, "entities")
    conn.commit()
    conn.close()
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return summary


def main():
    args = _parse_args()
    summary = build_load_entities(args.input, args.db, overwrite=args.overwrite)
    print("Load entities summary")
    print(f"  records_total: {summary['records_total']}")
    print(f"  rows_attempted: {summary['rows_attempted']}")
    print(f"  rows_inserted: {summary['rows_inserted']}")
    print(f"  rows_skipped: {summary['rows_skipped']}")
    print(f"  mentions_inserted: {summary['mentions_inserted']}")
    print(f"  json_decode_errors: {summary['json_decode_errors']}")
    print(f"  invalid_record_shape: {summary['invalid_record_shape']}")
    print(f"  invalid_item_shape: {summary['invalid_item_shape']}")
    print(f"  elapsed_seconds: {summary['elapsed_seconds']}")


if __name__ == "__main__":
    main()
