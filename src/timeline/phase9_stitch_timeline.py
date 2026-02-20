import argparse
import json
import time
from pathlib import Path

from file_parser.compress_io import open_text_reader
from loaders.store import connect_db, ensure_schema
from timeline.date_parse import (
    find_first_absolute_anchor,
    hash_redacted,
    parse_absolute_date_raw,
    parse_iso_datetime_to_date,
    parse_relative_spec,
    resolve_relative,
)


MAX_REDACTED_SAMPLES = 10


def _parse_args():
    parser = argparse.ArgumentParser(description="Normalize dates and stitch timelines in SQLite (Phase 9).")
    parser.add_argument(
        "--db",
        default="output/store.sqlite",
        help="SQLite DB path (default: output/store.sqlite).",
    )
    parser.add_argument(
        "--chunks",
        default=None,
        help="Optional chunks.jsonl path to derive chunk-level anchor dates.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Optional Phase 0 manifest.jsonl path to derive file-level anchor dates (mtime).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear existing event_times/event_cases rows before rebuilding.",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=None,
        help="Optional limit on events processed (for debugging).",
    )
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=0,
        help="Print progress every N seconds (0 disables).",
    )
    return parser.parse_args()


def _maybe_print_progress(processed, started, last_print, interval):
    if interval <= 0:
        return last_print
    now = time.monotonic()
    if now - last_print < interval:
        return last_print
    elapsed = round(now - started, 3)
    print(f"Timeline progress: {processed} elapsed_seconds={elapsed}", flush=True)
    return now


def _iter_jsonl(path: Path):
    with open_text_reader(path) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                yield json.loads(line), None
            except json.JSONDecodeError:
                yield None, "json_decode_error"


def _load_event_case_links(conn):
    links = {}
    cursor = conn.execute(
        "SELECT e.event_id, s.value, s.value_norm "
        "FROM events e "
        "JOIN identity_signals s "
        "ON e.file_id = s.file_id AND e.chunk_id = s.chunk_id "
        "WHERE s.attribute = 'case_id' AND s.value_norm IS NOT NULL"
    )
    for event_id, value, value_norm in cursor:
        if event_id is None or value_norm is None:
            continue
        links.setdefault(int(event_id), set()).add((str(value), str(value_norm)))
    return links


def _load_chunk_anchors(chunks_path: Path, needed_keys: set[tuple[str, str]]):
    anchors = {}
    if not needed_keys or chunks_path is None:
        return anchors
    chunks_path = Path(chunks_path)
    if not chunks_path.exists():
        raise SystemExit(f"Chunks not found: {chunks_path}")

    for record, error in _iter_jsonl(chunks_path):
        if error or not isinstance(record, dict):
            continue
        file_id = record.get("file_id")
        chunk_id = record.get("chunk_id")
        if not isinstance(file_id, str) or not isinstance(chunk_id, str):
            continue
        key = (file_id, chunk_id)
        if key not in needed_keys or key in anchors:
            continue
        anchor = find_first_absolute_anchor(record.get("text") or "")
        if anchor:
            anchors[key] = anchor
        if len(anchors) >= len(needed_keys):
            break
    return anchors


def _load_manifest_anchors(manifest_path: Path, needed_file_ids: set[str]):
    anchors = {}
    if not needed_file_ids or manifest_path is None:
        return anchors
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}")
    for record, error in _iter_jsonl(manifest_path):
        if error or not isinstance(record, dict):
            continue
        file_id = record.get("file_id")
        if not isinstance(file_id, str) or file_id not in needed_file_ids or file_id in anchors:
            continue
        anchor = parse_iso_datetime_to_date(record.get("mtime"))
        if anchor:
            anchors[file_id] = anchor
        if len(anchors) >= len(needed_file_ids):
            break
    return anchors


def _load_files_table_anchors(conn, needed_file_ids: set[str]):
    anchors = {}
    if not needed_file_ids:
        return anchors
    file_ids = sorted(needed_file_ids)
    batch_size = 500
    for offset in range(0, len(file_ids), batch_size):
        batch = file_ids[offset : offset + batch_size]
        placeholders = ",".join("?" for _ in batch)
        for file_id, mtime_utc in conn.execute(
            f"SELECT file_id, mtime_utc FROM files WHERE file_id IN ({placeholders})",
            batch,
        ):
            anchor = parse_iso_datetime_to_date(mtime_utc)
            if anchor:
                anchors[str(file_id)] = anchor
    return anchors


def build_stitch_timeline(
    db_path,
    chunks_path=None,
    manifest_path=None,
    reset=False,
    max_events=None,
    progress_interval=0,
):
    conn = connect_db(db_path)
    ensure_schema(conn, overwrite=False)

    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS timeline_pending_relative ("
        "event_id INTEGER PRIMARY KEY,"
        "file_id TEXT NOT NULL,"
        "chunk_id TEXT NOT NULL"
        ")"
    )
    conn.execute("DELETE FROM timeline_pending_relative")
    conn.commit()

    if reset:
        conn.execute("DELETE FROM event_cases")
        conn.execute("DELETE FROM event_times")
        conn.commit()

    cases_by_event = _load_event_case_links(conn)

    summary = {
        "events_total": 0,
        "events_with_nonempty_date_raw": 0,
        "normalized_ok": 0,
        "unresolved_relative": 0,
        "missing_anchor": 0,
        "invalid_format": 0,
        "unresolved_ambiguous": 0,
        "empty": 0,
        "case_links_identity_signals": 0,
        "case_links_fallback": 0,
    }
    samples = []
    started = time.monotonic()
    last_progress = started

    cursor = conn.execute("SELECT event_id, date, file_id, chunk_id FROM events ORDER BY event_id")
    for event_id, date_raw, file_id, chunk_id in cursor:
        if max_events is not None and summary["events_total"] >= max_events:
            break
        summary["events_total"] += 1
        last_progress = _maybe_print_progress(
            summary["events_total"], started, last_progress, progress_interval
        )

        date_raw_text = date_raw if isinstance(date_raw, str) else ""
        if date_raw_text.strip():
            summary["events_with_nonempty_date_raw"] += 1

        absolute, status = parse_absolute_date_raw(date_raw_text)
        if status == "empty":
            summary["empty"] += 1
            conn.execute(
                "INSERT OR REPLACE INTO event_times("
                "event_id, date_raw, date_start, date_end, precision, status, parser, anchor_date, notes_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (int(event_id), date_raw_text, None, None, "unknown", "empty", None, None, None),
            )
        elif status == "unresolved_ambiguous":
            summary["unresolved_ambiguous"] += 1
            if len(samples) < MAX_REDACTED_SAMPLES:
                samples.append({"status": "unresolved_ambiguous", "date_hash": hash_redacted(date_raw_text)})
            conn.execute(
                "INSERT OR REPLACE INTO event_times("
                "event_id, date_raw, date_start, date_end, precision, status, parser, anchor_date, notes_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(event_id),
                    date_raw_text,
                    None,
                    None,
                    "unknown",
                    "unresolved_ambiguous",
                    "absolute_v1",
                    None,
                    json.dumps(["multiple_absolute_dates"], ensure_ascii=True),
                ),
            )
        elif status == "invalid_format":
            summary["invalid_format"] += 1
            if len(samples) < MAX_REDACTED_SAMPLES:
                samples.append({"status": "invalid_format", "date_hash": hash_redacted(date_raw_text)})
            conn.execute(
                "INSERT OR REPLACE INTO event_times("
                "event_id, date_raw, date_start, date_end, precision, status, parser, anchor_date, notes_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(event_id),
                    date_raw_text,
                    None,
                    None,
                    "unknown",
                    "invalid_format",
                    "absolute_v1",
                    None,
                    json.dumps(["invalid_range"], ensure_ascii=True),
                ),
            )
        elif absolute is not None:
            summary["normalized_ok"] += 1
            conn.execute(
                "INSERT OR REPLACE INTO event_times("
                "event_id, date_raw, date_start, date_end, precision, status, parser, anchor_date, notes_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(event_id),
                    date_raw_text,
                    absolute.date_start,
                    absolute.date_end,
                    absolute.precision,
                    "ok",
                    absolute.parser,
                    None,
                    None,
                ),
            )
        else:
            spec = parse_relative_spec(date_raw_text)
            if spec is None:
                summary["invalid_format"] += 1
                if len(samples) < MAX_REDACTED_SAMPLES:
                    samples.append({"status": "invalid_format", "date_hash": hash_redacted(date_raw_text)})
                conn.execute(
                    "INSERT OR REPLACE INTO event_times("
                    "event_id, date_raw, date_start, date_end, precision, status, parser, anchor_date, notes_json"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        int(event_id),
                        date_raw_text,
                        None,
                        None,
                        "unknown",
                        "invalid_format",
                        None,
                        None,
                        None,
                    ),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO timeline_pending_relative(event_id, file_id, chunk_id) "
                    "VALUES (?, ?, ?)",
                    (int(event_id), str(file_id), str(chunk_id)),
                )

        # Event-case links (always rebuild per event for determinism).
        conn.execute("DELETE FROM event_cases WHERE event_id = ?", (int(event_id),))
        linked = cases_by_event.get(int(event_id)) or set()
        if linked:
            for case_id, case_id_norm in sorted(linked):
                conn.execute(
                    "INSERT OR IGNORE INTO event_cases(event_id, case_id, case_id_norm, source) "
                    "VALUES (?, ?, ?, ?)",
                    (int(event_id), case_id, case_id_norm, "identity_signals"),
                )
                summary["case_links_identity_signals"] += 1
        else:
            fallback = f"file:{file_id}"
            conn.execute(
                "INSERT OR IGNORE INTO event_cases(event_id, case_id, case_id_norm, source) "
                "VALUES (?, ?, ?, ?)",
                (int(event_id), fallback, fallback, "fallback"),
            )
            summary["case_links_fallback"] += 1

        if summary["events_total"] % 2000 == 0:
            conn.commit()

    # Resolve pending relative dates once anchors are available.
    pending_keys = {
        (str(file_id), str(chunk_id))
        for file_id, chunk_id in conn.execute(
            "SELECT DISTINCT file_id, chunk_id FROM timeline_pending_relative"
        )
    }
    chunk_anchors = _load_chunk_anchors(Path(chunks_path) if chunks_path else None, pending_keys)
    missing_file_anchors = {
        file_id
        for file_id, chunk_id in pending_keys
        if (file_id, chunk_id) not in chunk_anchors
    }

    file_anchors = {}
    if manifest_path:
        file_anchors = _load_manifest_anchors(Path(manifest_path), missing_file_anchors)
    else:
        file_anchors = _load_files_table_anchors(conn, missing_file_anchors)

    pending_cursor = conn.execute(
        "SELECT p.event_id, e.date, p.file_id, p.chunk_id "
        "FROM timeline_pending_relative p "
        "JOIN events e ON e.event_id = p.event_id "
        "ORDER BY p.event_id"
    )
    for event_id, date_raw, file_id, chunk_id in pending_cursor:
        date_raw_text = date_raw if isinstance(date_raw, str) else ""
        spec = parse_relative_spec(date_raw_text)
        if spec is None:
            summary["unresolved_relative"] += 1
            if len(samples) < MAX_REDACTED_SAMPLES:
                samples.append({"status": "unresolved_relative", "date_hash": hash_redacted(date_raw_text)})
            conn.execute(
                "INSERT OR REPLACE INTO event_times("
                "event_id, date_raw, date_start, date_end, precision, status, parser, anchor_date, notes_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(event_id),
                    date_raw_text,
                    None,
                    None,
                    "unknown",
                    "unresolved_relative",
                    "relative_v1",
                    None,
                    json.dumps(["unparseable_relative"], ensure_ascii=True),
                ),
            )
            continue

        anchor = chunk_anchors.get((file_id, chunk_id)) or file_anchors.get(file_id)
        if not anchor:
            summary["missing_anchor"] += 1
            if len(samples) < MAX_REDACTED_SAMPLES:
                samples.append({"status": "missing_anchor", "date_hash": hash_redacted(date_raw_text)})
            conn.execute(
                "INSERT OR REPLACE INTO event_times("
                "event_id, date_raw, date_start, date_end, precision, status, parser, anchor_date, notes_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(event_id),
                    date_raw_text,
                    None,
                    None,
                    "unknown",
                    "missing_anchor",
                    "relative_v1",
                    None,
                    None,
                ),
            )
            continue

        resolved, status = resolve_relative(spec, anchor)
        if status is None and resolved is not None:
            summary["normalized_ok"] += 1
            conn.execute(
                "INSERT OR REPLACE INTO event_times("
                "event_id, date_raw, date_start, date_end, precision, status, parser, anchor_date, notes_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(event_id),
                    date_raw_text,
                    resolved.date_start,
                    resolved.date_end,
                    resolved.precision,
                    "ok",
                    resolved.parser,
                    anchor,
                    None,
                ),
            )
        else:
            summary["unresolved_relative"] += 1
            if len(samples) < MAX_REDACTED_SAMPLES:
                samples.append({"status": "unresolved_relative", "date_hash": hash_redacted(date_raw_text)})
            conn.execute(
                "INSERT OR REPLACE INTO event_times("
                "event_id, date_raw, date_start, date_end, precision, status, parser, anchor_date, notes_json"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    int(event_id),
                    date_raw_text,
                    None,
                    None,
                    "unknown",
                    "unresolved_relative",
                    "relative_v1",
                    anchor,
                    None,
                ),
            )

    conn.commit()
    conn.close()
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    summary["redacted_samples"] = samples
    return summary


def main():
    args = _parse_args()
    summary = build_stitch_timeline(
        args.db,
        chunks_path=args.chunks,
        manifest_path=args.manifest,
        reset=args.reset,
        max_events=args.max_events,
        progress_interval=args.progress_interval,
    )
    print("Timeline stitch summary")
    for key in (
        "events_total",
        "events_with_nonempty_date_raw",
        "normalized_ok",
        "unresolved_relative",
        "missing_anchor",
        "invalid_format",
        "unresolved_ambiguous",
        "empty",
        "case_links_identity_signals",
        "case_links_fallback",
        "elapsed_seconds",
    ):
        print(f"  {key}: {summary.get(key)}")
    samples = summary.get("redacted_samples") or []
    if samples:
        print("  redacted_samples:")
        for sample in samples[:MAX_REDACTED_SAMPLES]:
            print(f"    - status={sample.get('status')} date_hash={sample.get('date_hash')}")


if __name__ == "__main__":
    main()
