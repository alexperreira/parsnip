import argparse
import json
import os
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from shutil import which


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


def _parse_zip_virtual_path(virtual_path):
    prefix = "zip://"
    if not virtual_path.startswith(prefix):
        return None, None
    remainder = virtual_path[len(prefix) :]
    if "::" not in remainder:
        return None, None
    container_relpath, inner_path = remainder.split("::", 1)
    return container_relpath, inner_path


def _copy_stream_to_tempfile(stream, suffix):
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            tmp.write(chunk)
    finally:
        tmp.close()
    return tmp.name


def _open_pdf_as_tempfile(record, root_path):
    source_type = record.get("source_type")
    if source_type == "fs":
        virtual_path = record.get("virtual_path")
        if not virtual_path:
            raise ValueError("Missing virtual_path")
        pdf_path = root_path / virtual_path
        return str(pdf_path)
    if source_type == "zip":
        container_path = record.get("container_path")
        virtual_path = record.get("virtual_path")
        if not container_path or not virtual_path:
            raise ValueError("Missing zip paths")
        _, inner_path = _parse_zip_virtual_path(virtual_path)
        if not inner_path:
            raise ValueError("Invalid zip virtual_path")
        zip_path = root_path / container_path
        with zipfile.ZipFile(zip_path, "r") as zf:
            with zf.open(inner_path) as entry:
                return _copy_stream_to_tempfile(entry, suffix=".pdf")
    raise ValueError("Unknown source_type")


def _write_text_page(text_dir, file_id, page_index, text):
    page_dir = Path(text_dir) / file_id
    page_dir.mkdir(parents=True, exist_ok=True)
    page_path = page_dir / f"page_{page_index}.txt"
    page_path.write_text(text, encoding="utf-8")
    return page_path.as_posix()


def _pending_record(record, classification, error_code):
    return {
        "file_id": record.get("file_id"),
        "source_type": record.get("source_type"),
        "container_path": record.get("container_path"),
        "virtual_path": record.get("virtual_path"),
        "size_bytes": record.get("size_bytes"),
        "mtime": record.get("mtime"),
        "ext": record.get("ext"),
        "classification": classification,
        "ocr_engine": "pending",
        "status": "pending_ocr",
        "page_count": record.get("page_count"),
        "pages": [],
        "errors": error_code,
    }


def _ensure_engine_dependencies():
    return which("tesseract") is not None and which("pdftoppm") is not None


def _ocr_with_tesseract(
    record,
    classification,
    root_path,
    lang,
    dpi,
    max_pages,
    text_dir,
    page_timeout,
):
    if not _ensure_engine_dependencies():
        return _pending_record(record, classification, "MissingTesseractOrPdftoppm")

    temp_pdf = None
    temp_dir = tempfile.TemporaryDirectory()
    pages = []
    errors = None
    try:
        temp_pdf = _open_pdf_as_tempfile(record, root_path)
        page_count = record.get("page_count")
        if page_count is None:
            return _pending_record(record, classification, "MissingPageCount")
        total_pages = int(page_count)
        if max_pages is not None:
            total_pages = min(total_pages, max_pages)

        for page_index in range(total_pages):
            render_path = Path(temp_dir.name) / f"page_{page_index}"
            render_cmd = [
                "pdftoppm",
                "-f",
                str(page_index + 1),
                "-l",
                str(page_index + 1),
                "-r",
                str(dpi),
                "-png",
                "-singlefile",
                temp_pdf,
                str(render_path),
            ]
            try:
                subprocess.run(render_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                image_path = f"{render_path}.png"
                ocr_cmd = [
                    "tesseract",
                    image_path,
                    "stdout",
                    "-l",
                    lang,
                    "--dpi",
                    str(dpi),
                ]
                ocr_run = subprocess.run(
                    ocr_cmd,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=page_timeout,
                )
                text = ocr_run.stdout.decode("utf-8", errors="replace")
                if text_dir:
                    text_path = _write_text_page(text_dir, record["file_id"], page_index, text)
                    pages.append({"page_index": page_index, "text_path": text_path, "confidence": None})
                else:
                    pages.append({"page_index": page_index, "text": text, "confidence": None})
            except (subprocess.SubprocessError, OSError):
                pages.append({"page_index": page_index, "text": "", "confidence": None, "errors": "OcrError"})
                errors = "OcrError"

        return {
            "file_id": record.get("file_id"),
            "source_type": record.get("source_type"),
            "container_path": record.get("container_path"),
            "virtual_path": record.get("virtual_path"),
            "size_bytes": record.get("size_bytes"),
            "mtime": record.get("mtime"),
            "ext": record.get("ext"),
            "classification": classification,
            "ocr_engine": "tesseract",
            "status": "ocr_complete",
            "page_count": record.get("page_count"),
            "pages": pages,
            "errors": errors,
        }
    finally:
        if temp_pdf and record.get("source_type") == "zip":
            try:
                os.remove(temp_pdf)
            except OSError:
                pass
        temp_dir.cleanup()


def build_phase2(
    input_path,
    phase1_path,
    output_path,
    resume=False,
    include_unknown=False,
    engine="tesseract",
    lang="eng",
    dpi=300,
    max_pages=None,
    text_dir=None,
    page_timeout=120,
    workers=1,
    ordered=False,
):
    root_path = Path(input_path).resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise SystemExit("Input path must be an existing directory.")

    phase1_path = Path(phase1_path)
    if not phase1_path.exists():
        raise SystemExit("Phase 1 path does not exist.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    resume_index = None
    resume_db_path = None
    skipped_existing = 0
    if resume:
        if output_path.exists():
            with tempfile.NamedTemporaryFile(prefix="phase2_resume_", suffix=".sqlite", delete=False) as tmp:
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
    counts_by_class = {"scanned": 0, "mixed": 0, "unknown": 0}
    started = time.monotonic()
    errors = 0

    if ordered and workers > 1:
        workers = 1

    def _process_record(classification, record):
        if engine != "tesseract":
            return classification, _pending_record(record, classification, "UnsupportedEngine")
        return classification, _ocr_with_tesseract(
            record,
            classification,
            root_path,
            lang=lang,
            dpi=dpi,
            max_pages=max_pages,
            text_dir=text_dir,
            page_timeout=page_timeout,
        )

    with output_path.open(output_mode, encoding="utf-8") as out_handle:
        if workers <= 1:
            with phase1_path.open("r", encoding="utf-8") as phase1_handle:
                for line in phase1_handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("ext") != "pdf":
                        continue
                    classification = record.get("classification", "unknown")
                    if classification not in ("scanned", "mixed", "unknown"):
                        classification = "unknown"
                    if classification == "unknown" and not include_unknown:
                        continue
                    if classification == "text":
                        continue
                    file_id = record.get("file_id")
                    if not file_id:
                        continue
                    if resume_index and not resume_index.add(file_id):
                        skipped += 1
                        continue
                    _, output_record = _process_record(classification, record)
                    if output_record.get("errors"):
                        errors += 1
                    out_handle.write(json.dumps(output_record, ensure_ascii=True) + "\n")
                    written += 1
                    counts_by_class[classification] += 1
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = set()
                max_in_flight = max(1, workers * 2)
                with phase1_path.open("r", encoding="utf-8") as phase1_handle:
                    for line in phase1_handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if record.get("ext") != "pdf":
                            continue
                        classification = record.get("classification", "unknown")
                        if classification not in ("scanned", "mixed", "unknown"):
                            classification = "unknown"
                        if classification == "unknown" and not include_unknown:
                            continue
                        if classification == "text":
                            continue
                        file_id = record.get("file_id")
                        if not file_id:
                            continue
                        if resume_index and not resume_index.add(file_id):
                            skipped += 1
                            continue

                        futures.add(executor.submit(_process_record, classification, record))
                        if len(futures) >= max_in_flight:
                            done = next(as_completed(futures))
                            futures.remove(done)
                            classification_out, output_record = done.result()
                            if output_record.get("errors"):
                                errors += 1
                            out_handle.write(json.dumps(output_record, ensure_ascii=True) + "\n")
                            written += 1
                            counts_by_class[classification_out] += 1

                for future in as_completed(futures):
                    classification_out, output_record = future.result()
                    if output_record.get("errors"):
                        errors += 1
                    out_handle.write(json.dumps(output_record, ensure_ascii=True) + "\n")
                    written += 1
                    counts_by_class[classification_out] += 1

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
        "counts_by_class": counts_by_class,
        "elapsed_seconds": round(elapsed, 3),
        "errors": errors,
    }
    return summary


def _print_summary(summary):
    print("Phase 2 summary")
    print(f"  written: {summary['written']}")
    print(f"  skipped (resume): {summary['skipped']}")
    print(f"  skipped (existing): {summary['skipped_existing']}")
    print(f"  class scanned: {summary['counts_by_class']['scanned']}")
    print(f"  class mixed: {summary['counts_by_class']['mixed']}")
    print(f"  class unknown: {summary['counts_by_class']['unknown']}")
    print(f"  elapsed_seconds: {summary['elapsed_seconds']}")
    if summary["errors"]:
        print(f"  errors: {summary['errors']}")


def _parse_args():
    parser = argparse.ArgumentParser(description="Phase 2 OCR.")
    parser.add_argument("--input", required=True, help="Input root to resolve files.")
    parser.add_argument("--phase1", required=True, help="Phase 1 JSONL path.")
    parser.add_argument(
        "--output",
        default="output/phase2_ocr.jsonl",
        help="Output JSONL path.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume by skipping file_ids already in the output.",
    )
    parser.add_argument(
        "--include-unknown",
        action="store_true",
        help="Include unknown classification files.",
    )
    parser.add_argument(
        "--engine",
        default="tesseract",
        help="OCR engine (default: tesseract).",
    )
    parser.add_argument("--lang", default="eng", help="OCR language (tesseract).")
    parser.add_argument("--dpi", type=int, default=300, help="Render DPI.")
    parser.add_argument("--max-pages", type=int, default=None, help="Max pages to OCR.")
    parser.add_argument(
        "--text-dir",
        default=None,
        help="Optional directory for per-page text outputs.",
    )
    parser.add_argument(
        "--page-timeout",
        type=int,
        default=120,
        help="Timeout per page OCR (seconds).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of concurrent workers.",
    )
    parser.add_argument(
        "--ordered",
        action="store_true",
        help="Write output in input order (forces single worker).",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    summary = build_phase2(
        args.input,
        args.phase1,
        args.output,
        resume=args.resume,
        include_unknown=args.include_unknown,
        engine=args.engine,
        lang=args.lang,
        dpi=args.dpi,
        max_pages=args.max_pages,
        text_dir=args.text_dir,
        page_timeout=args.page_timeout,
        workers=args.workers,
        ordered=args.ordered,
    )
    _print_summary(summary)


if __name__ == "__main__":
    main()
