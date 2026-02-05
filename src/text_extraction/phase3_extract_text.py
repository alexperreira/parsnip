import argparse
import gzip
import json
import tempfile
import zipfile
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


TEXT_CLASSIFICATIONS = {"text"}
OCR_CLASSIFICATIONS = {"scanned", "mixed", "unknown"}
DEFAULT_SHARD_SIZE = 5000


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


def build_phase3(input_path, phase1_path, output_dir, phase2_path=None, shard_size=DEFAULT_SHARD_SIZE):
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

    written = 0
    errors = 0
    shard_index = 0
    shard_written = 0
    shard_start = 0
    shard_handle = None
    shard_name = None
    manifest = []

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
                shard_start = written
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
            written += 1
            shard_written += 1

            if shard_written >= shard_size:
                shard_handle.close()
                manifest.append(
                    {
                        "shard": shard_name,
                        "start_index": shard_start,
                        "end_index": written - 1,
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
                    "end_index": written - 1,
                    "doc_count": shard_written,
                }
            )

    manifest_path = output_dir / "manifest.json"
    manifest_payload = {"shard_size": shard_size, "shards": manifest}
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest_payload, handle, ensure_ascii=True, indent=2)
        handle.write("\n")

    summary = {"written": written, "errors": errors}
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
    )
    print("Phase 3 summary")
    print(f"  written: {summary['written']}")
    if summary["errors"]:
        print(f"  errors: {summary['errors']}")


if __name__ == "__main__":
    main()
