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

from file_parser.phase1_detect import LOW_TEXT_MAX_CHARS
from file_parser.phase1_detect import TEXT_PAGE_MIN_CHARS
from file_parser.pdf_page_signals import inspect_pdf_pages
from file_parser.pdf_page_signals import mixed_page_ocr_decision


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


def _render_pdf_page_range(temp_pdf, output_dir, dpi, start_page, end_page):
    render_prefix = Path(output_dir) / "page"
    render_cmd = [
        "pdftoppm",
        "-f",
        str(start_page),
        "-l",
        str(end_page),
        "-r",
        str(dpi),
        "-png",
        temp_pdf,
        str(render_prefix),
    ]
    subprocess.run(render_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return render_prefix


def _count_phase1_targets(phase1_path, include_unknown):
    total = 0
    with phase1_path.open("r", encoding="utf-8") as handle:
        for line in handle:
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
            total += 1
    return total


def _maybe_print_progress(processed, total, started, last_print, interval):
    if interval <= 0 or total <= 0:
        return last_print
    now = time.monotonic()
    if now - last_print < interval:
        return last_print
    percent = processed / total * 100
    elapsed = round(now - started, 3)
    print(f"Progress: {processed}/{total} ({percent:.1f}%) elapsed_seconds={elapsed}", flush=True)
    return now


def _iter_render_chunks(page_numbers, batch_size):
    if not page_numbers:
        return
    chunk = [page_numbers[0]]
    for page_number in page_numbers[1:]:
        is_contiguous = page_number == chunk[-1] + 1
        if is_contiguous and len(chunk) < batch_size:
            chunk.append(page_number)
            continue
        yield chunk
        chunk = [page_number]
    yield chunk


def _default_page_plan(classification, total_pages):
    reason = "mixed_mode_all" if classification == "mixed" else "classification_requires_ocr"
    return [
        {
            "ocr_decision": "ocr",
            "ocr_reason": reason,
            "signal_text_chars": 0,
            "signal_has_image": False,
        }
        for _ in range(total_pages)
    ]


def _mixed_image_heavy_page_plan(temp_pdf, total_pages, text_page_min_chars, low_text_max_chars):
    page_plan = [
        {
            "ocr_decision": "ocr",
            "ocr_reason": "missing_signal_fallback_ocr",
            "signal_text_chars": 0,
            "signal_has_image": False,
        }
        for _ in range(total_pages)
    ]
    try:
        page_signals = inspect_pdf_pages(temp_pdf)
    except Exception:
        return page_plan
    for signal in page_signals:
        page_index = int(signal.get("page_index") or 0)
        if page_index < 0 or page_index >= total_pages:
            continue
        text_char_count = int(signal.get("text_char_count") or 0)
        has_image = bool(signal.get("has_image"))
        decision, reason = mixed_page_ocr_decision(
            text_char_count=text_char_count,
            has_image=has_image,
            text_page_min_chars=text_page_min_chars,
            low_text_max_chars=low_text_max_chars,
        )
        page_plan[page_index] = {
            "ocr_decision": decision,
            "ocr_reason": reason,
            "signal_text_chars": text_char_count,
            "signal_has_image": has_image,
        }
    return page_plan


def _build_page_record(page_index, page_plan):
    return {
        "page_index": page_index,
        "text": "",
        "confidence": None,
        "ocr_decision": page_plan["ocr_decision"],
        "ocr_reason": page_plan["ocr_reason"],
        "signal_text_chars": page_plan["signal_text_chars"],
        "signal_has_image": page_plan["signal_has_image"],
    }


def _ocr_with_tesseract(
    record,
    classification,
    root_path,
    lang,
    dpi,
    max_pages,
    text_dir,
    page_timeout,
    page_workers,
    skip_low_signal_bytes,
    mixed_ocr_mode,
    text_page_min_chars,
    low_text_max_chars,
):
    temp_pdf = None
    temp_dir = tempfile.TemporaryDirectory()
    errors = None
    try:
        temp_pdf = _open_pdf_as_tempfile(record, root_path)
        page_count = record.get("page_count")
        if page_count is None:
            return _pending_record(record, classification, "MissingPageCount")
        total_pages = int(page_count)
        if max_pages is not None:
            total_pages = min(total_pages, max_pages)
        if total_pages <= 0:
            return _pending_record(record, classification, "InvalidPageCount")

        if page_workers is None or page_workers < 1:
            page_workers = 1
        batch_size = max(1, page_workers * 2)

        if classification == "mixed" and mixed_ocr_mode == "image-heavy":
            page_plan = _mixed_image_heavy_page_plan(
                temp_pdf,
                total_pages,
                text_page_min_chars=text_page_min_chars,
                low_text_max_chars=low_text_max_chars,
            )
        else:
            page_plan = _default_page_plan(classification, total_pages)

        pages = [_build_page_record(page_index, page_plan[page_index]) for page_index in range(total_pages)]
        pages_to_ocr = [page_index for page_index, plan in enumerate(page_plan) if plan["ocr_decision"] == "ocr"]

        if pages_to_ocr and not _ensure_engine_dependencies():
            return _pending_record(record, classification, "MissingTesseractOrPdftoppm")

        def _ocr_page(page_index, image_path):
            try:
                if not Path(image_path).is_file():
                    raise FileNotFoundError(image_path)
                if skip_low_signal_bytes and skip_low_signal_bytes > 0:
                    image_size = Path(image_path).stat().st_size
                    if image_size <= skip_low_signal_bytes:
                        return (
                            page_index,
                            {
                                "text": "",
                                "errors": "SkippedLowSignal",
                            },
                            None,
                        )
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
                    return page_index, {"text_path": text_path}, None
                return page_index, {"text": text}, None
            except (subprocess.SubprocessError, OSError):
                return (
                    page_index,
                    {"text": "", "errors": "OcrError"},
                    "OcrError",
                )

        page_numbers_to_ocr = sorted(page_index + 1 for page_index in pages_to_ocr)
        render_chunks = list(_iter_render_chunks(page_numbers_to_ocr, batch_size))

        if page_workers <= 1 or len(page_numbers_to_ocr) <= 1:
            for chunk_index, chunk in enumerate(render_chunks):
                batch_start = chunk[0]
                batch_end = chunk[-1]
                try:
                    render_prefix = _render_pdf_page_range(
                        temp_pdf,
                        temp_dir.name,
                        dpi,
                        batch_start,
                        batch_end,
                    )
                except (subprocess.SubprocessError, OSError):
                    errors = "OcrError"
                    for pending_chunk in render_chunks[chunk_index:]:
                        for page_number in pending_chunk:
                            pages[page_number - 1]["errors"] = "OcrError"
                    break
                for page_number in chunk:
                    page_index = page_number - 1
                    image_path = f"{render_prefix}-{page_number}.png"
                    page_index, page_record, page_error = _ocr_page(page_index, image_path)
                    pages[page_index].update(page_record)
                    if page_error:
                        errors = "OcrError"
                    try:
                        os.remove(image_path)
                    except OSError:
                        pass
        else:
            with ThreadPoolExecutor(max_workers=page_workers) as executor:
                for chunk_index, chunk in enumerate(render_chunks):
                    batch_start = chunk[0]
                    batch_end = chunk[-1]
                    try:
                        render_prefix = _render_pdf_page_range(
                            temp_pdf,
                            temp_dir.name,
                            dpi,
                            batch_start,
                            batch_end,
                        )
                    except (subprocess.SubprocessError, OSError):
                        errors = "OcrError"
                        for pending_chunk in render_chunks[chunk_index:]:
                            for page_number in pending_chunk:
                                pages[page_number - 1]["errors"] = "OcrError"
                        break
                    futures = set()
                    for page_number in chunk:
                        page_index = page_number - 1
                        image_path = f"{render_prefix}-{page_number}.png"
                        futures.add(executor.submit(_ocr_page, page_index, image_path))
                    for done in as_completed(futures):
                        page_index, page_record, page_error = done.result()
                        pages[page_index].update(page_record)
                        if page_error:
                            errors = "OcrError"
                    for page_number in chunk:
                        try:
                            os.remove(f"{render_prefix}-{page_number}.png")
                        except OSError:
                            pass

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
    page_workers=1,
    skip_low_signal_bytes=0,
    mixed_ocr_mode="image-heavy",
    text_page_min_chars=TEXT_PAGE_MIN_CHARS,
    low_text_max_chars=LOW_TEXT_MAX_CHARS,
    workers=1,
    ordered=False,
    progress_interval=0,
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
    processed = 0
    progress_total = 0
    last_progress = started

    if ordered and workers > 1:
        workers = 1
    cpu_count = os.cpu_count() or 1
    if page_workers < 1:
        page_workers = 1
    if workers < 1:
        workers = 1
    requested_page_workers = page_workers
    max_page_workers = max(1, cpu_count // workers)
    if mixed_ocr_mode not in ("all", "image-heavy"):
        raise SystemExit("--mixed-ocr-mode must be one of: all, image-heavy")
    if requested_page_workers > max_page_workers:
        page_workers = max_page_workers
        print(
            "Warning: capping page_workers to avoid CPU over-subscription "
            f"(requested={requested_page_workers}, cap={page_workers}, workers={workers}, cpu={cpu_count}).",
            flush=True,
        )
    if workers * page_workers > cpu_count:
        print(
            "Warning: workers * page_workers exceeds CPU count "
            f"(workers={workers}, page_workers={page_workers}, cpu={cpu_count}).",
            flush=True,
        )
    if progress_interval > 0:
        progress_total = _count_phase1_targets(phase1_path, include_unknown)

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
            page_workers=page_workers,
            skip_low_signal_bytes=skip_low_signal_bytes,
            mixed_ocr_mode=mixed_ocr_mode,
            text_page_min_chars=text_page_min_chars,
            low_text_max_chars=low_text_max_chars,
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
                        processed += 1
                        last_progress = _maybe_print_progress(
                            processed,
                            progress_total,
                            started,
                            last_progress,
                            progress_interval,
                        )
                        continue
                    _, output_record = _process_record(classification, record)
                    if output_record.get("errors"):
                        errors += 1
                    out_handle.write(json.dumps(output_record, ensure_ascii=True) + "\n")
                    written += 1
                    processed += 1
                    counts_by_class[classification] += 1
                    last_progress = _maybe_print_progress(
                        processed,
                        progress_total,
                        started,
                        last_progress,
                        progress_interval,
                    )
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
                            processed += 1
                            last_progress = _maybe_print_progress(
                                processed,
                                progress_total,
                                started,
                                last_progress,
                                progress_interval,
                            )
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
                            processed += 1
                            counts_by_class[classification_out] += 1
                            last_progress = _maybe_print_progress(
                                processed,
                                progress_total,
                                started,
                                last_progress,
                                progress_interval,
                            )

                for future in as_completed(futures):
                    classification_out, output_record = future.result()
                    if output_record.get("errors"):
                        errors += 1
                    out_handle.write(json.dumps(output_record, ensure_ascii=True) + "\n")
                    written += 1
                    processed += 1
                    counts_by_class[classification_out] += 1
                    last_progress = _maybe_print_progress(
                        processed,
                        progress_total,
                        started,
                        last_progress,
                        progress_interval,
                    )

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
    parser.add_argument(
        "--mixed-ocr-mode",
        default="image-heavy",
        choices=("all", "image-heavy"),
        help="For mixed PDFs, OCR all pages or only low-text/image-heavy pages.",
    )
    parser.add_argument(
        "--text-page-min-chars",
        type=int,
        default=TEXT_PAGE_MIN_CHARS,
        help="Text chars threshold for considering a mixed page text-bearing.",
    )
    parser.add_argument(
        "--low-text-max-chars",
        type=int,
        default=LOW_TEXT_MAX_CHARS,
        help="Max text chars to treat a mixed page as low-text for OCR routing.",
    )
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
        "--page-workers",
        type=int,
        default=1,
        help="Number of concurrent OCR workers per PDF.",
    )
    parser.add_argument(
        "--skip-low-signal-bytes",
        type=int,
        default=0,
        help="Skip OCR for pages with rendered PNG size <= N bytes (0 disables).",
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
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=0,
        help="Print progress every N seconds (0 disables).",
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
        mixed_ocr_mode=args.mixed_ocr_mode,
        text_page_min_chars=args.text_page_min_chars,
        low_text_max_chars=args.low_text_max_chars,
        max_pages=args.max_pages,
        text_dir=args.text_dir,
        page_timeout=args.page_timeout,
        page_workers=args.page_workers,
        skip_low_signal_bytes=args.skip_low_signal_bytes,
        workers=args.workers,
        ordered=args.ordered,
        progress_interval=args.progress_interval,
    )
    _print_summary(summary)


if __name__ == "__main__":
    main()
