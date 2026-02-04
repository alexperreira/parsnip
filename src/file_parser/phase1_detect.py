import argparse
import json
import os
import sqlite3
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


TEXT_PAGE_MIN_CHARS = 50
LOW_TEXT_MAX_CHARS = 10
TEXT_RATIO_MIN = 0.6
IMAGE_RATIO_MIN = 0.6
IMAGE_RATIO_MAX_FOR_TEXT = 0.2
LOW_TEXT_RATIO_MIN = 0.8
MAX_SAMPLE_PAGES = 20


@dataclass(frozen=True)
class DetectionConfig:
    text_page_min_chars: int = TEXT_PAGE_MIN_CHARS
    low_text_max_chars: int = LOW_TEXT_MAX_CHARS
    text_ratio_min: float = TEXT_RATIO_MIN
    image_ratio_min: float = IMAGE_RATIO_MIN
    image_ratio_max_for_text: float = IMAGE_RATIO_MAX_FOR_TEXT
    low_text_ratio_min: float = LOW_TEXT_RATIO_MIN
    max_sample_pages: int = MAX_SAMPLE_PAGES


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


def _normalize_zip_inner_path(inner_path):
    return inner_path.replace("\\", "/")


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


def _page_has_image(page):
    try:
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject")
        if not xobjects:
            return False
        for obj in xobjects.values():
            try:
                resolved = obj.get_object()
            except Exception:
                resolved = obj
            try:
                subtype = resolved.get("/Subtype")
            except Exception:
                continue
            if subtype == "/Image":
                return True
    except Exception:
        return False
    return False


def _classify(text_pages, image_pages, low_text_pages, sample_pages, config):
    if sample_pages == 0:
        return "unknown"
    text_ratio = text_pages / sample_pages
    image_ratio = image_pages / sample_pages
    low_text_ratio = low_text_pages / sample_pages
    if text_ratio >= config.text_ratio_min and image_ratio <= config.image_ratio_max_for_text:
        return "text"
    if image_ratio >= config.image_ratio_min and low_text_ratio >= config.low_text_ratio_min:
        return "scanned"
    if text_pages > 0 and image_pages > 0:
        return "mixed"
    if text_pages > 0:
        return "text"
    if image_pages > 0:
        return "scanned"
    return "unknown"


def detect_pdf(file_obj, config):
    result = {
        "page_count": None,
        "text_char_count_total": 0,
        "text_pages": 0,
        "image_pages": 0,
        "low_text_pages": 0,
        "sampled": False,
        "classification": "unknown",
        "errors": None,
    }
    try:
        reader = PdfReader(file_obj, strict=False)
        page_count = len(reader.pages)
        result["page_count"] = page_count
        sample_pages = min(page_count, config.max_sample_pages)
        result["sampled"] = sample_pages < page_count
        for i in range(sample_pages):
            page = reader.pages[i]
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            text_len = len(text)
            result["text_char_count_total"] += text_len
            if text_len >= config.text_page_min_chars:
                result["text_pages"] += 1
            if text_len <= config.low_text_max_chars:
                result["low_text_pages"] += 1
            if _page_has_image(page):
                result["image_pages"] += 1
        result["classification"] = _classify(
            result["text_pages"],
            result["image_pages"],
            result["low_text_pages"],
            sample_pages,
            config,
        )
    except (PdfReadError, OSError, ValueError) as exc:
        result["errors"] = type(exc).__name__
    return result


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


def _parse_zip_virtual_path(virtual_path):
    prefix = "zip://"
    if not virtual_path.startswith(prefix):
        return None, None
    remainder = virtual_path[len(prefix) :]
    if "::" not in remainder:
        return None, None
    container_relpath, inner_path = remainder.split("::", 1)
    return container_relpath, inner_path


def _maybe_print_progress(processed, started, last_print, interval):
    if interval <= 0:
        return last_print
    now = time.monotonic()
    if now - last_print < interval:
        return last_print
    elapsed = round(now - started, 3)
    print(f"Phase 1 progress: {processed} elapsed_seconds={elapsed}", flush=True)
    return now


def build_phase1(
    manifest_path,
    input_path,
    output_path,
    resume=False,
    config=None,
    progress_interval=0,
):
    if config is None:
        config = DetectionConfig()

    root_path = Path(input_path).resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise SystemExit("Input path must be an existing directory.")

    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        raise SystemExit("Manifest path does not exist.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    resume_index = None
    resume_db_path = None
    skipped_existing = 0
    if resume:
        if output_path.exists():
            with tempfile.NamedTemporaryFile(prefix="phase1_resume_", suffix=".sqlite", delete=False) as tmp:
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
    counts_by_class = {"text": 0, "scanned": 0, "mixed": 0, "unknown": 0}
    started = time.monotonic()
    errors = 0
    processed = 0
    last_progress = started

    with manifest_path.open("r", encoding="utf-8") as manifest_handle:
        with output_path.open(output_mode, encoding="utf-8") as out_handle:
            for line in manifest_handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("ext") != "pdf":
                    continue
                file_id = record.get("file_id")
                if not file_id:
                    continue
                if resume_index and not resume_index.add(file_id):
                    skipped += 1
                    processed += 1
                    last_progress = _maybe_print_progress(
                        processed,
                        started,
                        last_progress,
                        progress_interval,
                    )
                    continue

                source_type = record.get("source_type")
                container_path = record.get("container_path")
                virtual_path = record.get("virtual_path")

                detection = None
                try:
                    if source_type == "fs":
                        if not virtual_path:
                            raise ValueError("Missing virtual_path")
                        fs_path = root_path / virtual_path
                        with fs_path.open("rb") as handle:
                            detection = detect_pdf(handle, config)
                    elif source_type == "zip":
                        if not virtual_path or not container_path:
                            raise ValueError("Missing zip paths")
                        _, inner_path = _parse_zip_virtual_path(virtual_path)
                        if not inner_path:
                            raise ValueError("Invalid zip virtual_path")
                        inner_path = _normalize_zip_inner_path(inner_path)
                        zip_path = root_path / container_path
                        pdf_stream = _open_zip_entry(zip_path, inner_path)
                        try:
                            detection = detect_pdf(pdf_stream, config)
                        finally:
                            pdf_stream.close()
                    else:
                        detection = {"classification": "unknown", "errors": "UnknownSource"}
                except Exception:
                    detection = {"classification": "unknown", "errors": "DetectError"}
                    errors += 1

                output_record = {
                    "file_id": file_id,
                    "source_type": source_type,
                    "container_path": container_path,
                    "virtual_path": virtual_path,
                    "size_bytes": record.get("size_bytes"),
                    "mtime": record.get("mtime"),
                    "ext": record.get("ext"),
                    "page_count": detection.get("page_count"),
                    "text_char_count_total": detection.get("text_char_count_total", 0),
                    "text_pages": detection.get("text_pages", 0),
                    "image_pages": detection.get("image_pages", 0),
                    "low_text_pages": detection.get("low_text_pages", 0),
                    "sampled": detection.get("sampled"),
                    "classification": detection.get("classification", "unknown"),
                    "errors": detection.get("errors"),
                }

                out_handle.write(json.dumps(output_record, ensure_ascii=True) + "\n")
                written += 1
                counts_by_class[output_record["classification"]] += 1
                processed += 1
                last_progress = _maybe_print_progress(
                    processed,
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
    print("Phase 1 summary")
    print(f"  written: {summary['written']}")
    print(f"  skipped (resume): {summary['skipped']}")
    print(f"  skipped (existing): {summary['skipped_existing']}")
    print(f"  class text: {summary['counts_by_class']['text']}")
    print(f"  class scanned: {summary['counts_by_class']['scanned']}")
    print(f"  class mixed: {summary['counts_by_class']['mixed']}")
    print(f"  class unknown: {summary['counts_by_class']['unknown']}")
    print(f"  elapsed_seconds: {summary['elapsed_seconds']}")
    if summary["errors"]:
        print(f"  errors: {summary['errors']}")


def _parse_args():
    parser = argparse.ArgumentParser(description="Phase 1 PDF detection (no OCR).")
    parser.add_argument("--input", required=True, help="Input root to resolve files.")
    parser.add_argument("--manifest", required=True, help="Phase 0 manifest JSONL.")
    parser.add_argument(
        "--output",
        default="output/phase1.jsonl",
        help="Output JSONL detection path.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume by skipping file_ids already in the output.",
    )
    parser.add_argument("--max-sample-pages", type=int, default=MAX_SAMPLE_PAGES)
    parser.add_argument("--text-page-min-chars", type=int, default=TEXT_PAGE_MIN_CHARS)
    parser.add_argument("--low-text-max-chars", type=int, default=LOW_TEXT_MAX_CHARS)
    parser.add_argument("--text-ratio-min", type=float, default=TEXT_RATIO_MIN)
    parser.add_argument("--image-ratio-min", type=float, default=IMAGE_RATIO_MIN)
    parser.add_argument("--image-ratio-max-for-text", type=float, default=IMAGE_RATIO_MAX_FOR_TEXT)
    parser.add_argument("--low-text-ratio-min", type=float, default=LOW_TEXT_RATIO_MIN)
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=0,
        help="Print progress every N seconds (0 disables).",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    config = DetectionConfig(
        text_page_min_chars=args.text_page_min_chars,
        low_text_max_chars=args.low_text_max_chars,
        text_ratio_min=args.text_ratio_min,
        image_ratio_min=args.image_ratio_min,
        image_ratio_max_for_text=args.image_ratio_max_for_text,
        low_text_ratio_min=args.low_text_ratio_min,
        max_sample_pages=args.max_sample_pages,
    )
    summary = build_phase1(
        args.manifest,
        args.input,
        args.output,
        resume=args.resume,
        config=config,
        progress_interval=args.progress_interval,
    )
    _print_summary(summary)


if __name__ == "__main__":
    main()
