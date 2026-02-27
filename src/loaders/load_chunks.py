import argparse
import hashlib
import json
import time
from pathlib import Path

from file_parser.compress_io import open_text_reader
from loaders.store import as_clean_text, connect_db, ensure_schema, mark_loader_run


def _parse_args():
    parser = argparse.ArgumentParser(description="Load chunks JSONL into SQLite.")
    parser.add_argument(
        "--input",
        default="chunks.jsonl",
        help="Chunks JSONL path or directory (default: chunks.jsonl).",
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


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("-"):
            sign = -1
            digits = stripped[1:]
        else:
            sign = 1
            digits = stripped
        if digits and digits.isdigit():
            return sign * int(digits)
    return None


def _iter_source_paths(input_path: Path):
    if input_path.is_file():
        yield input_path
        return

    if not input_path.is_dir():
        raise SystemExit("Input path must be a file or directory.")

    shard_paths = []
    manifest_path = input_path / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            manifest = None
        if isinstance(manifest, dict):
            for shard in manifest.get("shards") or []:
                if not isinstance(shard, dict):
                    continue
                shard_name = shard.get("shard")
                if isinstance(shard_name, str) and shard_name.startswith("chunks"):
                    shard_paths.append(input_path / shard_name)
    if not shard_paths:
        for pattern in (
            "chunks_*.jsonl.zst",
            "chunks_*.jsonl.gz",
            "chunks_*.jsonl",
            "chunks.jsonl.zst",
            "chunks.jsonl.gz",
            "chunks.jsonl",
        ):
            matches = sorted(input_path.glob(pattern))
            if matches:
                shard_paths = matches
                break
    if not shard_paths:
        raise SystemExit(
            "No chunks files found in input directory "
            "(supported: chunks*.jsonl.zst, chunks*.jsonl.gz, chunks*.jsonl)."
        )

    for shard_path in shard_paths:
        if not shard_path.exists():
            raise SystemExit(f"Chunks shard missing: {shard_path}")
        yield shard_path


def _iter_jsonl_with_offsets(path: Path):
    with open_text_reader(path) as handle:
        while True:
            byte_start = None
            byte_end = None
            try:
                byte_start = handle.tell()
            except (AttributeError, OSError):
                byte_start = None

            raw_line = handle.readline()
            if not raw_line:
                break

            try:
                byte_end = handle.tell()
            except (AttributeError, OSError):
                byte_end = None

            line = raw_line.strip()
            if not line:
                continue
            try:
                yield json.loads(line), None, byte_start, byte_end
            except json.JSONDecodeError:
                yield None, "json_decode_error", byte_start, byte_end


def _normalize_signals_json(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return None


def _build_text_ref(storage_path: str, chunk_id: str, byte_start, byte_end):
    if isinstance(byte_start, int) and isinstance(byte_end, int) and byte_end >= byte_start:
        return f"jsonl://{storage_path}#byte={byte_start}-{byte_end}"
    return f"jsonl://{storage_path}#chunk_id={chunk_id}"


def _text_hash(text):
    if not isinstance(text, str) or text == "":
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_load_chunks(input_path, db_path, overwrite=False):
    input_path = Path(input_path)
    source_paths = list(_iter_source_paths(input_path))
    conn = connect_db(db_path)
    ensure_schema(conn, overwrite=overwrite)

    summary = {
        "files_total": len(source_paths),
        "records_total": 0,
        "rows_attempted": 0,
        "rows_inserted": 0,
        "rows_skipped": 0,
        "text_refs_upserted": 0,
        "json_decode_errors": 0,
        "invalid_record_shape": 0,
        "invalid_page_range": 0,
    }
    started = time.monotonic()

    for source_path in source_paths:
        storage_path = str(source_path.resolve())
        for record, error, byte_start, byte_end in _iter_jsonl_with_offsets(source_path):
            summary["records_total"] += 1
            if error:
                summary["json_decode_errors"] += 1
                continue
            if not isinstance(record, dict):
                summary["invalid_record_shape"] += 1
                continue

            chunk_id = as_clean_text(record.get("chunk_id"))
            file_id = as_clean_text(record.get("file_id"))
            page_start = _as_int(record.get("page_start"))
            page_end = _as_int(record.get("page_end"))
            if not chunk_id or not file_id or page_start is None or page_end is None:
                summary["invalid_record_shape"] += 1
                continue
            if page_start < 0 or page_end < 0 or page_end < page_start:
                summary["invalid_page_range"] += 1
                continue

            signals_json = _normalize_signals_json(record.get("signals"))
            text_ref = _build_text_ref(storage_path, chunk_id, byte_start, byte_end)
            summary["rows_attempted"] += 1
            result = conn.execute(
                "INSERT OR IGNORE INTO chunks("
                "chunk_id, file_id, page_start, page_end, signals_json, text_ref"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (chunk_id, file_id, int(page_start), int(page_end), signals_json, text_ref),
            )
            if result.rowcount == 1:
                summary["rows_inserted"] += 1
            else:
                summary["rows_skipped"] += 1

            text = record.get("text")
            conn.execute(
                "INSERT INTO chunk_text_refs("
                "chunk_id, storage_path, byte_start, byte_end, text_char_count, text_sha256, created_utc"
                ") VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(chunk_id) DO UPDATE SET "
                "storage_path=excluded.storage_path, "
                "byte_start=excluded.byte_start, "
                "byte_end=excluded.byte_end, "
                "text_char_count=excluded.text_char_count, "
                "text_sha256=excluded.text_sha256, "
                "created_utc=excluded.created_utc",
                (
                    chunk_id,
                    storage_path,
                    byte_start if isinstance(byte_start, int) else None,
                    byte_end if isinstance(byte_end, int) else None,
                    len(text) if isinstance(text, str) else None,
                    _text_hash(text),
                ),
            )
            summary["text_refs_upserted"] += 1

    mark_loader_run(conn, "chunks")
    conn.commit()
    conn.close()
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return summary


def main():
    args = _parse_args()
    summary = build_load_chunks(args.input, args.db, overwrite=args.overwrite)
    print("Load chunks summary")
    print(f"  files_total: {summary['files_total']}")
    print(f"  records_total: {summary['records_total']}")
    print(f"  rows_attempted: {summary['rows_attempted']}")
    print(f"  rows_inserted: {summary['rows_inserted']}")
    print(f"  rows_skipped: {summary['rows_skipped']}")
    print(f"  text_refs_upserted: {summary['text_refs_upserted']}")
    print(f"  json_decode_errors: {summary['json_decode_errors']}")
    print(f"  invalid_record_shape: {summary['invalid_record_shape']}")
    print(f"  invalid_page_range: {summary['invalid_page_range']}")
    print(f"  elapsed_seconds: {summary['elapsed_seconds']}")


if __name__ == "__main__":
    main()
