import argparse
import json
import re
import sqlite3
from pathlib import Path

from file_parser.compress_io import open_text_reader
DEFAULT_CHUNK_SIZE = 2
DEFAULT_OVERLAP = 1
DIALOGUE_RE = re.compile(
    r"(\bQ:\s|\bA:\s|\bName:\s|\b[A-Z][a-z]{1,20}:\s|\u2014|\"[^\"]{1,200}\")",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"(\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|"
    r"\b(?:Jan|January|Feb|February|Mar|March|Apr|April|May|Jun|June|Jul|July|"
    r"Aug|August|Sep|Sept|September|Oct|October|Nov|November|Dec|December)"
    r"\s+\d{1,2},\s+\d{4}\b)"
)
NAME_RE = re.compile(r"\b[A-Z][a-z]+\s+[A-Z][a-z]+\b")

def _open_jsonl(path: Path):
    return open_text_reader(path)

def _iter_jsonl(path: Path):
    with _open_jsonl(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield record

def _iter_records(input_path: Path):
    if input_path.is_dir():
        manifest_path = input_path / "manifest.json"
        manifest = None
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                manifest = None
        if manifest:
            shard_names = [entry.get("shard") for entry in manifest.get("shards") or []]
            shard_paths = [input_path / name for name in shard_names if name]
        else:
            shard_paths = []
            for pattern in ("docs_*.jsonl.zst", "docs_*.jsonl.gz", "docs_*.jsonl"):
                matches = sorted(input_path.glob(pattern))
                if matches:
                    shard_paths = matches
                    break
        if not shard_paths:
            raise SystemExit(
                "No shard files found in input directory "
                "(supported: docs_*.jsonl.zst, docs_*.jsonl.gz, docs_*.jsonl)."
            )
        for shard_path in shard_paths:
            if not shard_path.exists():
                raise SystemExit(f"Shard missing: {shard_path.name}")
            yield from _iter_jsonl(shard_path)
        return
    if input_path.is_file():
        yield from _iter_jsonl(input_path)
        return
    raise SystemExit("Input path must be a file or directory.")

def _chunk_low_quality(pages, chunk_text: str) -> bool:
    if any(page.get("review_required") for page in pages):
        return True
    stripped = chunk_text.strip()
    if not stripped or len(stripped) < 40:
        return True
    confidences = [
        page.get("confidence")
        for page in pages
        if page.get("confidence") is not None
    ]
    return bool(confidences) and (sum(confidences) / len(confidences)) < 0.5

def _normalize_pages(pages):
    normalized = []
    for idx, page in enumerate(pages):
        if not isinstance(page, dict):
            continue
        page_index = page.get("page_index")
        try:
            page_index = int(page_index)
        except (TypeError, ValueError):
            page_index = idx
        text = page.get("text") or ""
        if not isinstance(text, str):
            text = str(text)
        normalized.append(
            {
                "page_index": page_index,
                "text": text,
                "review_required": bool(page.get("review_required")),
                "confidence": page.get("confidence"),
                "dialogue": bool(text) and DIALOGUE_RE.search(text[:4000]) is not None,
            }
        )
    normalized.sort(key=lambda item: item["page_index"])
    return normalized

def _build_chunks(file_id, pages, chunk_size, overlap):
    if not pages:
        return []
    step = max(1, chunk_size - overlap)
    chunks = []
    index = 0
    while index < len(pages):
        if pages[index]["dialogue"] or (
            index + 1 < len(pages) and pages[index + 1]["dialogue"]
        ):
            start = end = index
        else:
            start = index
            end = min(index + chunk_size - 1, len(pages) - 1)
            if pages[end]["dialogue"] and end > index:
                end = index
        chunk_pages = pages[start : end + 1]
        page_start = chunk_pages[0]["page_index"]
        page_end = chunk_pages[-1]["page_index"]
        chunk_text = "\n\n".join(
            page["text"] for page in chunk_pages if page.get("text")
        )
        chunks.append(
            {
                "chunk_id": f"{file_id}:{page_start}-{page_end}",
                "file_id": file_id,
                "page_start": page_start,
                "page_end": page_end,
                "text": chunk_text,
                "signals": {
                    "likely_dialogue": any(page["dialogue"] for page in chunk_pages),
                    "has_dates": bool(chunk_text) and DATE_RE.search(chunk_text[:8000]) is not None,
                    "has_names": bool(chunk_text) and NAME_RE.search(chunk_text[:8000]) is not None,
                    "low_quality": _chunk_low_quality(chunk_pages, chunk_text),
                },
            }
        )
        index += 1 if end == index else step
    return chunks

def _index_db_path(output_path: Path) -> Path:
    return output_path.with_suffix(".sqlite")


def _ensure_chunk_schema(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
    if "file_id" not in columns:
        conn.execute("ALTER TABLE chunks ADD COLUMN file_id TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_file_id ON chunks(file_id)")
    conn.commit()


def _ensure_chunk_index(db_path: Path, output_path: Path, append: bool):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if append and not output_path.exists() and db_path.exists():
        raise SystemExit(
            "Chunk index exists without output file; move the index aside or use --overwrite."
        )
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, file_id TEXT)"
    )
    _ensure_chunk_schema(conn)
    conn.commit()
    if append and output_path.exists():
        cursor = conn.execute("SELECT COUNT(*) FROM chunks")
        row = cursor.fetchone()
        if row and int(row[0]) == 0:
            _load_existing_chunks(conn, output_path)
    return conn


def _load_existing_chunks(conn, output_path: Path):
    pending = 0
    for record in _iter_jsonl(output_path):
        chunk_id = record.get("chunk_id")
        file_id = record.get("file_id")
        if not chunk_id:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO chunks(chunk_id, file_id) VALUES (?, ?)",
            (chunk_id, file_id),
        )
        pending += 1
        if pending >= 10000:
            conn.commit()
            pending = 0
    conn.commit()


def _load_replace_file_ids(path):
    ids = set()
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"Replace file_ids list not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            ids.add(line)
    if not ids:
        raise SystemExit("Replace file_ids list is empty.")
    return ids


def _rewrite_output_without_file_ids(output_path: Path, replace_ids):
    tmp_output = output_path.with_name(output_path.name + ".tmp")
    tmp_db = _index_db_path(output_path).with_suffix(".sqlite.tmp")
    removed = 0
    kept = 0

    conn = sqlite3.connect(tmp_db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, file_id TEXT)"
    )
    _ensure_chunk_schema(conn)

    with output_path.open("r", encoding="utf-8") as read_handle, tmp_output.open(
        "w", encoding="utf-8"
    ) as write_handle:
        pending = 0
        for line in read_handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            file_id = record.get("file_id")
            if file_id in replace_ids:
                removed += 1
                continue
            chunk_id = record.get("chunk_id")
            if not chunk_id:
                continue
            write_handle.write(line + "\n")
            conn.execute(
                "INSERT OR IGNORE INTO chunks(chunk_id, file_id) VALUES (?, ?)",
                (chunk_id, file_id),
            )
            pending += 1
            kept += 1
            if pending >= 10000:
                conn.commit()
                pending = 0
    conn.commit()
    conn.close()

    tmp_output.replace(output_path)
    tmp_db.replace(_index_db_path(output_path))
    return removed, kept


def build_phase4(
    input_path,
    output_path,
    chunk_size,
    overlap,
    overwrite=False,
    append=False,
    replace_file_ids_path=None,
):
    output_path = Path(output_path)
    if append and overwrite:
        raise SystemExit("Choose either --append or --overwrite, not both.")
    if replace_file_ids_path and overwrite:
        raise SystemExit("Replace-by-file_id cannot be used with --overwrite.")
    if output_path.exists() and not overwrite and not append:
        raise SystemExit("Output path exists; choose a new location.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    docs_seen = 0
    chunks_written = 0
    errors = 0
    skipped_existing = 0
    removed_existing = 0
    index_conn = None
    replace_ids = None
    if replace_file_ids_path:
        replace_ids = _load_replace_file_ids(replace_file_ids_path)
        if not output_path.exists():
            raise SystemExit("Replace-by-file_id requires existing output to update.")
        removed_existing, _ = _rewrite_output_without_file_ids(output_path, replace_ids)
        append = True
    if append:
        index_conn = _ensure_chunk_index(_index_db_path(output_path), output_path, append)
    mode = "a" if append and output_path.exists() else "w"
    with output_path.open(mode, encoding="utf-8") as handle:
        for record in _iter_records(Path(input_path)):
            file_id = record.get("file_id")
            if not file_id:
                continue
            pages = _normalize_pages(record.get("pages") or [])
            if not pages:
                errors += 1
                continue
            for chunk in _build_chunks(file_id, pages, chunk_size, overlap):
                if index_conn is not None:
                    try:
                        index_conn.execute(
                            "INSERT INTO chunks(chunk_id, file_id) VALUES (?, ?)",
                            (chunk["chunk_id"], chunk["file_id"]),
                        )
                    except sqlite3.IntegrityError:
                        skipped_existing += 1
                        continue
                handle.write(json.dumps(chunk, ensure_ascii=True) + "\n")
                chunks_written += 1
            docs_seen += 1
    if index_conn is not None:
        index_conn.commit()
        index_conn.close()
    return {
        "docs_seen": docs_seen,
        "chunks_written": chunks_written,
        "errors": errors,
        "skipped_existing": skipped_existing,
        "removed_existing": removed_existing,
    }

def _parse_args():
    parser = argparse.ArgumentParser(description="Phase 4 chunking for analysis.")
    parser.add_argument("--input", required=True, help="Phase 3 output dir or shard file.")
    parser.add_argument("--output", default=None, help="Output JSONL path.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output file if it already exists.",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to output and skip existing chunk_ids via a sqlite index.",
    )
    parser.add_argument(
        "--replace-file-ids",
        default=None,
        help="Text file of file_id values to replace (one per line).",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="Pages per chunk (default: 2)."
    )
    parser.add_argument(
        "--overlap", type=int, default=DEFAULT_OVERLAP, help="Page overlap (default: 1)."
    )
    return parser.parse_args()

def main():
    args = _parse_args()
    input_path = Path(args.input)
    if args.output:
        output_path = Path(args.output)
    elif input_path.is_dir():
        output_path = input_path / "chunks.jsonl"
    else:
        output_path = input_path.parent / "chunks.jsonl"
    if args.chunk_size is None or args.chunk_size <= 0:
        raise SystemExit("Chunk size must be a positive integer.")
    if args.overlap is None or args.overlap < 0:
        raise SystemExit("Overlap must be a non-negative integer.")
    if args.overlap >= args.chunk_size:
        raise SystemExit("Overlap must be smaller than chunk size.")
    summary = build_phase4(
        input_path,
        output_path,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        overwrite=args.overwrite,
        append=args.append,
        replace_file_ids_path=args.replace_file_ids,
    )
    print("Phase 4 summary")
    print(f"  docs seen: {summary['docs_seen']}")
    print(f"  chunks written: {summary['chunks_written']}")
    if summary.get("removed_existing"):
        print(f"  removed (replaced file_ids): {summary['removed_existing']}")
    if summary.get("skipped_existing"):
        print(f"  skipped (existing): {summary['skipped_existing']}")
    if summary["errors"]:
        print(f"  errors: {summary['errors']}")
if __name__ == "__main__":
    main()
