import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def _isoformat_from_timestamp(ts):
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _isoformat_from_zipinfo(zip_info):
    try:
        return datetime(*zip_info.date_time, tzinfo=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _hash_file_id(source_type, container_path, virtual_path):
    basis = f"{source_type}|{container_path or ''}|{virtual_path}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class _ResumeIndex:
    def __init__(self, db_path):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("CREATE TABLE IF NOT EXISTS seen (file_id TEXT PRIMARY KEY)")
        self._conn.commit()
        self._pending = 0

    def add(self, file_id):
        try:
            self._conn.execute("INSERT INTO seen(file_id) VALUES (?)", (file_id,))
        except sqlite3.IntegrityError:
            return False
        self._pending += 1
        if self._pending >= 1000:
            self._conn.commit()
            self._pending = 0
        return True

    def close(self):
        self._conn.commit()
        self._conn.close()


def _normalize_relative_path(path, root_path):
    return Path(path).relative_to(root_path).as_posix()


def _normalize_zip_inner_path(inner_path):
    return inner_path.replace("\\", "/")


def _iter_fs_pdfs(root_path):
    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        dirnames.sort()
        filenames.sort()
        for filename in filenames:
            if filename.lower().endswith(".pdf"):
                yield Path(dirpath) / filename


def _iter_zip_files(root_path):
    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        dirnames.sort()
        filenames.sort()
        for filename in filenames:
            if filename.lower().endswith(".zip"):
                yield Path(dirpath) / filename


def _load_resume_index(output_path, resume_index):
    skipped = 0
    with output_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            file_id = record.get("file_id")
            if not file_id:
                continue
            if not resume_index.add(file_id):
                skipped += 1
    return skipped


def build_manifest(input_path, output_path, resume=False):
    root_path = Path(input_path).resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise SystemExit("Input path must be an existing directory.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    resume_index = None
    resume_db_path = None
    skipped_existing = 0
    if resume:
        if output_path.exists():
            with tempfile.NamedTemporaryFile(prefix="manifest_resume_", suffix=".sqlite", delete=False) as tmp:
                resume_db_path = tmp.name
            resume_index = _ResumeIndex(resume_db_path)
            skipped_existing = _load_resume_index(output_path, resume_index)
            output_mode = "a"
        else:
            output_mode = "w"
    else:
        if output_path.exists():
            raise SystemExit("Output path exists; use --resume to append safely.")
        output_mode = "w"

    written = 0
    skipped = 0
    counts_by_source = {"fs": 0, "zip": 0}
    errors = 0
    started = time.monotonic()

    with output_path.open(output_mode, encoding="utf-8") as out_handle:
        for fs_path in _iter_fs_pdfs(root_path):
            try:
                stat = fs_path.stat()
                virtual_path = _normalize_relative_path(fs_path, root_path)
                record = {
                    "file_id": _hash_file_id("fs", None, virtual_path),
                    "source_type": "fs",
                    "container_path": None,
                    "virtual_path": virtual_path,
                    "size_bytes": stat.st_size,
                    "mtime": _isoformat_from_timestamp(stat.st_mtime),
                    "ext": "pdf",
                }
                if resume_index and not resume_index.add(record["file_id"]):
                    skipped += 1
                    continue
                out_handle.write(json.dumps(record, ensure_ascii=True) + "\n")
                written += 1
                counts_by_source["fs"] += 1
            except OSError:
                errors += 1

        for zip_path in _iter_zip_files(root_path):
            try:
                container_path = _normalize_relative_path(zip_path, root_path)
                with zipfile.ZipFile(zip_path, "r") as zf:
                    for entry in zf.infolist():
                        inner_path = _normalize_zip_inner_path(entry.filename)
                        if not inner_path.lower().endswith(".pdf"):
                            continue
                        virtual_path = f"zip://{container_path}::{inner_path}"
                        record = {
                            "file_id": _hash_file_id("zip", container_path, virtual_path),
                            "source_type": "zip",
                            "container_path": container_path,
                            "virtual_path": virtual_path,
                            "size_bytes": entry.file_size,
                            "mtime": _isoformat_from_zipinfo(entry),
                            "ext": "pdf",
                        }
                        if resume_index and not resume_index.add(record["file_id"]):
                            skipped += 1
                            continue
                        out_handle.write(json.dumps(record, ensure_ascii=True) + "\n")
                        written += 1
                        counts_by_source["zip"] += 1
            except (OSError, zipfile.BadZipFile):
                errors += 1

    elapsed = time.monotonic() - started
    if resume_index:
        resume_index.close()
    if resume_db_path:
        try:
            os.remove(resume_db_path)
        except OSError:
            pass

    summary = {
        "written": written,
        "skipped": skipped,
        "skipped_existing": skipped_existing,
        "counts_by_source": counts_by_source,
        "elapsed_seconds": round(elapsed, 3),
        "errors": errors,
    }
    return summary


def _print_summary(summary):
    print("Manifest summary")
    print(f"  written: {summary['written']}")
    print(f"  skipped (resume): {summary['skipped']}")
    print(f"  skipped (existing): {summary['skipped_existing']}")
    print(f"  source_type fs: {summary['counts_by_source']['fs']}")
    print(f"  source_type zip: {summary['counts_by_source']['zip']}")
    print(f"  elapsed_seconds: {summary['elapsed_seconds']}")
    if summary["errors"]:
        print(f"  errors: {summary['errors']}")


def _parse_args():
    parser = argparse.ArgumentParser(description="Build a PDF manifest (Phase 0).")
    parser.add_argument("--input", required=True, help="Input root to scan.")
    parser.add_argument(
        "--output",
        default="output/manifest.jsonl",
        help="Output JSONL manifest path.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume by skipping file_ids already in the output.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    summary = build_manifest(args.input, args.output, resume=args.resume)
    _print_summary(summary)


if __name__ == "__main__":
    main()
