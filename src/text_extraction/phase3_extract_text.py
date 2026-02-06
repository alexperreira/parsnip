import argparse
import gzip
import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


TEXT_CLASSIFICATIONS = {"text"}
OCR_CLASSIFICATIONS = {"scanned", "mixed", "unknown"}
DEFAULT_SHARD_SIZE = 5000


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


def _parse_shard_index(shard_name):
    prefix = "docs_"
    suffix = ".jsonl.gz"
    if not shard_name.startswith(prefix) or not shard_name.endswith(suffix):
        return None
    index_text = shard_name[len(prefix) : -len(suffix)]
    if not index_text.isdigit():
        return None
    return int(index_text)


def _read_manifest(output_dir):
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _write_manifest(output_dir, payload):
    manifest_path = output_dir / "manifest.json"
    tmp_path = output_dir / "manifest.json.tmp"
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2)
        handle.write("\n")
    tmp_path.replace(manifest_path)


def _resume_index_count(resume_index):
    cursor = resume_index._conn.execute("SELECT COUNT(*) FROM seen")
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _read_shard_file(shard_path, resume_index):
    doc_count = 0
    with gzip.open(shard_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            file_id = record.get("file_id")
            if file_id:
                resume_index.add(file_id)
            doc_count += 1
    return doc_count


def _parse_zip_virtual_path(virtual_path):
    prefix = "zip://"
    if not virtual_path.startswith(prefix):
        return None, None
    remainder = virtual_path[len(prefix) :]
    if "::" not in remainder:
        return None, None
    container_relpath, inner_path = remainder.split("::", 1)
    return container_relpath, inner_path


def _open_zip_entry(zip_path, inner_path):
    zf = zipfile.ZipFile(zip_path, "r")
    entry = zf.open(inner_path)
    tmp = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024)
    try:
        while True:
            chunk = entry.read(1024 * 1024)
            if not chunk:
                break
            tmp.write(chunk)
        tmp.seek(0)
        return tmp
    finally:
        entry.close()
        zf.close()


def _iter_phase1_records(phase1_path):
    with phase1_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield record


def _index_phase2(phase2_path):
    by_file_id = {}
    if not phase2_path:
        return by_file_id
    phase2_path = Path(phase2_path)
    if not phase2_path.exists():
        return by_file_id
    with phase2_path.open("r", encoding="utf-8") as handle:
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
            by_file_id[file_id] = record
    return by_file_id


def _open_pdf_stream(record, root_path):
    source_type = record.get("source_type")
    if source_type == "fs":
        virtual_path = record.get("virtual_path")
        if not virtual_path:
            raise ValueError("Missing virtual_path")
        pdf_path = root_path / virtual_path
        return pdf_path.open("rb")
    if source_type == "zip":
        container_path = record.get("container_path")
        virtual_path = record.get("virtual_path")
        if not container_path or not virtual_path:
            raise ValueError("Missing zip paths")
        _, inner_path = _parse_zip_virtual_path(virtual_path)
        if not inner_path:
            raise ValueError("Invalid zip virtual_path")
        zip_path = root_path / container_path
        return _open_zip_entry(zip_path, inner_path)
    raise ValueError("Unknown source_type")


def _extract_pdf_text(record, root_path):
    pages = []
    try:
        with _open_pdf_stream(record, root_path) as handle:
            reader = PdfReader(handle, strict=False)
            for page_index, page in enumerate(reader.pages):
                try:
                    text = page.extract_text() or ""
                except Exception:
                    text = ""
                pages.append(
                    {
                        "page_index": page_index,
                        "text": text,
                        "source": "pdf_text",
                        "confidence": None,
                        "review_required": False,
                        "review_reason": None,
                    }
                )
    except (PdfReadError, OSError, ValueError):
        return None
    return pages


def _pages_from_ocr(phase2_record, page_count):
    pages = []
    ocr_pages = phase2_record.get("pages") or []
    for page_index in range(page_count):
        text = ""
        confidence = None
        source = "ocr"
        review_required = False
        review_reason = None
        if page_index < len(ocr_pages):
            page = ocr_pages[page_index] or {}
            text = page.get("text", "") or ""
            if not text:
                text_path = page.get("text_path")
                if text_path:
                    try:
                        text = Path(text_path).read_text(encoding="utf-8")
                    except OSError:
                        text = ""
                        review_required = True
                        review_reason = "unreadable_text_path"
                else:
                    review_required = True
                    review_reason = "missing_text_path"
            confidence = page.get("confidence")
        pages.append(
            {
                "page_index": page_index,
                "text": text,
                "source": source,
                "confidence": confidence,
                "review_required": review_required,
                "review_reason": review_reason,
            }
        )
    return pages


def _estimate_quality(pages):
    if not pages:
        return 0.0
    non_empty = sum(1 for page in pages if page.get("text"))
    text_ratio = non_empty / len(pages)
    confidences = [
        page.get("confidence")
        for page in pages
        if page.get("confidence") is not None
    ]
    if not confidences:
        return round(text_ratio, 6)
    avg_conf = sum(confidences) / len(confidences)
    if avg_conf > 1:
        avg_conf = 1.0
    return round((text_ratio + avg_conf) / 2, 6)


def _open_shard(output_dir, shard_index):
    shard_name = f"docs_{shard_index:04d}.jsonl.gz"
    shard_path = output_dir / shard_name
    handle = gzip.open(shard_path, "wt", encoding="utf-8")
    return shard_name, shard_path, handle


def build_phase3(
    input_path,
    phase1_path,
    output_dir,
    phase2_path=None,
    shard_size=DEFAULT_SHARD_SIZE,
    resume=False,
):
    root_path = Path(input_path).resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise SystemExit("Input path must be an existing directory.")

    phase1_path = Path(phase1_path)
    if not phase1_path.exists():
        raise SystemExit("Phase 1 path does not exist.")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if shard_size is None:
        raise SystemExit("Shard size must be provided.")
    try:
        shard_size = int(shard_size)
    except (TypeError, ValueError):
        raise SystemExit("Shard size must be an integer.")
    if shard_size <= 0:
        raise SystemExit("Shard size must be a positive integer.")

    phase2_index = _index_phase2(phase2_path)

    resume_index = None
    resume_db_path = None
    skipped_existing = 0
    skipped = 0
    total_written = 0
    new_written = 0
    errors = 0
    shard_index = 0
    shard_written = 0
    shard_start = 0
    shard_handle = None
    shard_name = None
    manifest = []

    if resume:
        resume_db_path = output_dir / "resume.db"
        resume_index = _ResumeIndex(resume_db_path)

        manifest_payload = _read_manifest(output_dir)
        manifest_entries = []
        manifest_shard_size = None
        if manifest_payload:
            manifest_entries = list(manifest_payload.get("shards", []))
            manifest_shard_size = manifest_payload.get("shard_size")

        if manifest_shard_size is not None:
            try:
                manifest_shard_size = int(manifest_shard_size)
            except (TypeError, ValueError):
                manifest_shard_size = None
        if manifest_shard_size is not None and manifest_shard_size != shard_size:
            raise SystemExit(
                "Shard size mismatch with existing manifest "
                f"({manifest_shard_size}); use --shard-size {manifest_shard_size}."
            )

        if manifest_entries:
            total_written = sum(int(entry.get("doc_count") or 0) for entry in manifest_entries)
            shard_index = max(
                (_parse_shard_index(entry.get("shard") or "") or 0) for entry in manifest_entries
            )
            for entry in manifest_entries:
                shard_name = entry.get("shard")
                if not shard_name:
                    continue
                shard_path = output_dir / shard_name
                if not shard_path.exists():
                    raise SystemExit(f"Manifest shard missing: {shard_name}")

        resume_count = _resume_index_count(resume_index)
        shard_files = sorted(path.name for path in output_dir.glob("docs_*.jsonl.gz"))
        if not manifest_entries and not shard_files and resume_count > 0:
            raise SystemExit(
                "resume.db exists without shards or manifest; "
                "move it aside or pass a new output directory."
            )

        if (resume_count == 0 or resume_count < total_written) and manifest_entries:
            for entry in manifest_entries:
                shard_name = entry.get("shard")
                if not shard_name:
                    continue
                shard_path = output_dir / shard_name
                try:
                    _read_shard_file(shard_path, resume_index)
                except OSError as exc:
                    raise SystemExit(f"Failed to read shard {shard_name}: {exc}") from exc

        manifest_set = {entry.get("shard") for entry in manifest_entries if entry.get("shard")}
        extra_shards = [name for name in shard_files if name not in manifest_set]
        if extra_shards:
            for shard_name in extra_shards:
                shard_path = output_dir / shard_name
                try:
                    doc_count = _read_shard_file(shard_path, resume_index)
                except OSError as exc:
                    raise SystemExit(f"Failed to read shard {shard_name}: {exc}") from exc
                if doc_count:
                    manifest_entries.append(
                        {
                            "shard": shard_name,
                            "start_index": total_written,
                            "end_index": total_written + doc_count - 1,
                            "doc_count": doc_count,
                        }
                    )
                    total_written += doc_count
                    shard_idx = _parse_shard_index(shard_name)
                    if shard_idx and shard_idx > shard_index:
                        shard_index = shard_idx

        manifest = manifest_entries
        if manifest_payload is None or extra_shards:
            _write_manifest(output_dir, {"shard_size": shard_size, "shards": manifest})

        total_written = sum(int(entry.get("doc_count") or 0) for entry in manifest)
        resume_count = _resume_index_count(resume_index)
        if shard_files and resume_count > total_written:
            manifest = []
            total_written = 0
            shard_index = 0
            for shard_name in shard_files:
                shard_path = output_dir / shard_name
                try:
                    doc_count = _read_shard_file(shard_path, resume_index)
                except OSError as exc:
                    raise SystemExit(f"Failed to read shard {shard_name}: {exc}") from exc
                if doc_count:
                    manifest.append(
                        {
                            "shard": shard_name,
                            "start_index": total_written,
                            "end_index": total_written + doc_count - 1,
                            "doc_count": doc_count,
                        }
                    )
                    total_written += doc_count
                    shard_idx = _parse_shard_index(shard_name)
                    if shard_idx and shard_idx > shard_index:
                        shard_index = shard_idx
            _write_manifest(output_dir, {"shard_size": shard_size, "shards": manifest})
            resume_count = _resume_index_count(resume_index)
            if resume_count > total_written:
                raise SystemExit(
                    "resume.db has more entries than available shards; "
                    "move resume.db aside or use a new output directory."
                )

    try:
        try:
            for record in _iter_phase1_records(phase1_path):
                if record.get("ext") != "pdf":
                    continue
                classification = record.get("classification", "unknown")
                if classification not in TEXT_CLASSIFICATIONS | OCR_CLASSIFICATIONS:
                    classification = "unknown"
                file_id = record.get("file_id")
                if not file_id:
                    continue
                if resume_index and not resume_index.add(file_id):
                    skipped += 1
                    continue
                page_count = record.get("page_count")
                if page_count is None:
                    page_count = 0
                try:
                    page_count = int(page_count)
                except (TypeError, ValueError):
                    page_count = 0

                pages = None
                if classification in TEXT_CLASSIFICATIONS:
                    pages = _extract_pdf_text(record, root_path)
                    if pages is None:
                        errors += 1
                if pages is None:
                    phase2_record = phase2_index.get(file_id)
                    if phase2_record is None:
                        pages = _pages_from_ocr({}, page_count)
                    else:
                        pages = _pages_from_ocr(phase2_record, page_count)

                if shard_handle is None:
                    shard_index += 1
                    shard_start = total_written
                    shard_name, _, shard_handle = _open_shard(output_dir, shard_index)

                output_record = {
                    "file_id": file_id,
                    "virtual_path": record.get("virtual_path"),
                    "classification": classification,
                    "page_count": page_count,
                    "quality_score": _estimate_quality(pages),
                    "pages": pages,
                }
                shard_handle.write(json.dumps(output_record, ensure_ascii=True) + "\n")
                total_written += 1
                new_written += 1
                shard_written += 1

                if shard_written >= shard_size:
                    shard_handle.close()
                    manifest.append(
                        {
                            "shard": shard_name,
                            "start_index": shard_start,
                            "end_index": total_written - 1,
                            "doc_count": shard_written,
                        }
                    )
                    shard_handle = None
                    shard_name = None
                    shard_written = 0
        finally:
            if shard_handle is not None:
                shard_handle.close()
                manifest.append(
                    {
                        "shard": shard_name,
                        "start_index": shard_start,
                        "end_index": total_written - 1,
                        "doc_count": shard_written,
                    }
                )

        manifest_payload = {"shard_size": shard_size, "shards": manifest}
        _write_manifest(output_dir, manifest_payload)
    finally:
        if resume_index:
            resume_index.close()

    summary = {
        "written": new_written,
        "errors": errors,
        "skipped": skipped,
        "skipped_existing": skipped_existing,
    }
    return summary


def _parse_args():
    parser = argparse.ArgumentParser(description="Phase 3 unified text extraction.")
    parser.add_argument("--input", required=True, help="Input root to resolve files.")
    parser.add_argument("--phase1", required=True, help="Phase 1 JSONL path.")
    parser.add_argument("--phase2", default=None, help="Phase 2 OCR JSONL path.")
    parser.add_argument(
        "--output-dir",
        default="output/text",
        help="Output directory for sharded JSONL.GZ files.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Deprecated. Use --output-dir instead.",
    )
    parser.add_argument(
        "--shard-size",
        default=DEFAULT_SHARD_SIZE,
        help="Documents per shard (default: 5000).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from existing output shards.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    output_dir = args.output_dir
    if args.output:
        output_dir = args.output
    summary = build_phase3(
        args.input,
        args.phase1,
        output_dir,
        phase2_path=args.phase2,
        shard_size=args.shard_size,
        resume=args.resume,
    )
    print("Phase 3 summary")
    print(f"  written: {summary['written']}")
    if summary["skipped_existing"]:
        print(f"  skipped (existing): {summary['skipped_existing']}")
    if summary["skipped"]:
        print(f"  skipped (resume): {summary['skipped']}")
    if summary["errors"]:
        print(f"  errors: {summary['errors']}")


if __name__ == "__main__":
    main()
