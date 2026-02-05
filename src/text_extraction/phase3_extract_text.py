import argparse
import json
import tempfile
import zipfile
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError


TEXT_CLASSIFICATIONS = {"text"}
OCR_CLASSIFICATIONS = {"scanned", "mixed", "unknown"}


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
            confidence = page.get("confidence")
        pages.append(
            {
                "page_index": page_index,
                "text": text,
                "source": source,
                "confidence": confidence,
            }
        )
    return pages


def _estimate_quality(pages):
    if not pages:
        return 0.0
    non_empty = sum(1 for page in pages if page.get("text"))
    return round(non_empty / len(pages), 6)


def build_phase3(input_path, phase1_path, output_path, phase2_path=None):
    root_path = Path(input_path).resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise SystemExit("Input path must be an existing directory.")

    phase1_path = Path(phase1_path)
    if not phase1_path.exists():
        raise SystemExit("Phase 1 path does not exist.")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    phase2_index = _index_phase2(phase2_path)

    written = 0
    errors = 0

    with output_path.open("w", encoding="utf-8") as out_handle:
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

            output_record = {
                "file_id": file_id,
                "virtual_path": record.get("virtual_path"),
                "classification": classification,
                "page_count": page_count,
                "quality_score": _estimate_quality(pages),
                "pages": pages,
            }
            out_handle.write(json.dumps(output_record, ensure_ascii=True) + "\n")
            written += 1

    summary = {"written": written, "errors": errors}
    return summary


def _parse_args():
    parser = argparse.ArgumentParser(description="Phase 3 unified text extraction.")
    parser.add_argument("--input", required=True, help="Input root to resolve files.")
    parser.add_argument("--phase1", required=True, help="Phase 1 JSONL path.")
    parser.add_argument("--phase2", default=None, help="Phase 2 OCR JSONL path.")
    parser.add_argument(
        "--output",
        default="output/phase3_text.jsonl",
        help="Output JSONL path.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    summary = build_phase3(
        args.input,
        args.phase1,
        args.output,
        phase2_path=args.phase2,
    )
    print("Phase 3 summary")
    print(f"  written: {summary['written']}")
    if summary["errors"]:
        print(f"  errors: {summary['errors']}")


if __name__ == "__main__":
    main()
