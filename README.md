# File Parser (v1)

This repo contains a Python-first file parsing pipeline. Phase 0 builds a manifest (no content extraction). Phase 1 performs PDF detection only (no OCR) to classify PDFs as text/scanned/mixed/unknown.

## Phase 0: Manifest builder (no content extraction)

Build a JSONL manifest of PDFs on disk and PDFs inside zip files:

```bash
PYTHONPATH=src python -m file_parser.manifest_builder --input /path/to/input --output output/manifest.jsonl
```

Resume mode skips duplicates using `file_id`:

```bash
PYTHONPATH=src python -m file_parser.manifest_builder --input /path/to/input --output output/manifest.jsonl --resume
```

Output fields include `file_id`, `source_type`, `container_path`, `virtual_path`, `size_bytes`, `mtime`, and `ext`.

## Phase 1: PDF detection (no OCR)

Phase 1 reads the Phase 0 manifest and classifies each PDF using lightweight signals.

```bash
PYTHONPATH=src python -m file_parser.phase1_detect --input /path/to/input --manifest output/manifest.jsonl --output output/phase1.jsonl
```

Resume mode skips duplicates using `file_id`:

```bash
PYTHONPATH=src python -m file_parser.phase1_detect --input /path/to/input --manifest output/manifest.jsonl --output output/phase1.jsonl --resume
```

### Defaults (deterministic)

- `text_page_min_chars`: 50
- `low_text_max_chars`: 10
- `text_ratio_min`: 0.6
- `image_ratio_min`: 0.6
- `image_ratio_max_for_text`: 0.2
- `low_text_ratio_min`: 0.8
- `max_sample_pages`: 20

### Output fields (adds to Phase 0)

- `page_count`
- `text_char_count_total`
- `text_pages`
- `image_pages`
- `low_text_pages`
- `sampled`
- `classification`
- `errors`

### Tuning guidance

If many PDFs are classified as `unknown`, reduce `text_page_min_chars` or increase `max_sample_pages`. If too many look like `scanned`, lower `image_ratio_min` or increase `low_text_max_chars`. Keep thresholds fixed for consistent results across runs.

## Phase 1 report (summary only)

Summarize Phase 1 output without printing file paths:

```bash
PYTHONPATH=src python -m file_parser.phase1_report --input output/phase1.jsonl
```

## Phase 2 OCR (tesseract adapter)

Phase 2 runs OCR for files classified as `scanned` or `mixed` in Phase 1. It uses `tesseract` and `pdftoppm` (Poppler) if installed. If either is missing, entries are written with `status: pending_ocr` and an error code.

```bash
PYTHONPATH=src python -m file_parser.phase2_ocr --input /path/to/input --phase1 output/phase1.jsonl --output output/phase2_ocr.jsonl
```

Include `unknown` classification files:

```bash
PYTHONPATH=src python -m file_parser.phase2_ocr --input /path/to/input --phase1 output/phase1.jsonl --output output/phase2_ocr.jsonl --include-unknown
```

Control concurrency:

```bash
PYTHONPATH=src python -m file_parser.phase2_ocr --input /path/to/input --phase1 output/phase1.jsonl --output output/phase2_ocr.jsonl --workers 4
```

Deterministic output order (forces single worker):

```bash
PYTHONPATH=src python -m file_parser.phase2_ocr --input /path/to/input --phase1 output/phase1.jsonl --output output/phase2_ocr.jsonl --ordered
```

Write per-page text files instead of inline text:

```bash
PYTHONPATH=src python -m file_parser.phase2_ocr --input /path/to/input --phase1 output/phase1.jsonl --output output/phase2_ocr.jsonl --text-dir output/ocr_text
```

## Dependencies

- Phase 0: Python standard library only
- Phase 1: `pypdf` (see `requirements.txt`)
