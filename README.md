# File Parser (v1)

This repo contains a Python-first file parsing pipeline. Phase 0 builds a manifest (no content extraction). Phase 1 performs PDF detection only (no OCR) to classify PDFs as text/scanned/mixed/unknown.

## End-to-end pipeline (Phase 0-2)

Run the full pipeline end-to-end:

```bash
PYTHONPATH=src python -m file_parser.run_pipeline --input /path/to/input --output-dir output
```

Common flags:

- `--resume` to append safely instead of failing on existing outputs
- `--include-unknown` to OCR `unknown` classifications
- `--workers N` and `--page-workers N` to control concurrency
- `--max-pages N` to cap OCR pages per PDF
- `--text-dir output/ocr_text` to write per-page text files
- `--progress-interval N` to print Phase 2 progress

## Make targets (shortcuts)

Run the pipeline with Make:

```bash
make all INPUT=/path/to/input
```

Available targets:

- `make manifest`
- `make phase1`
- `make report`
- `make phase2`
- `make phase3`
- `make test`
- `make print-output`
- `make clean`
- `make clean-all`

Variables you can override:

- `INPUT` (default `/path/to/input`)
- `OUT_DIR` (default `output`)

## Phase 0: Manifest builder (no content extraction)

Build a JSONL manifest of PDFs on disk and PDFs inside zip files:

```bash
PYTHONPATH=src python -m file_parser.manifest_builder --input /path/to/input --output output/manifest.jsonl
```

Resume mode skips duplicates using `file_id`:

```bash
PYTHONPATH=src python -m file_parser.manifest_builder --input /path/to/input --output output/manifest.jsonl --resume
```

Print periodic progress:

```bash
PYTHONPATH=src python -m file_parser.manifest_builder --input /path/to/input --output output/manifest.jsonl --progress-interval 5
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

Print periodic progress:

```bash
PYTHONPATH=src python -m file_parser.phase1_detect --input /path/to/input --manifest output/manifest.jsonl --output output/phase1.jsonl --progress-interval 5
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

Parallelize OCR within each PDF (balance CPU/memory use):

```bash
PYTHONPATH=src python -m file_parser.phase2_ocr --input /path/to/input --phase1 output/phase1.jsonl --output output/phase2_ocr.jsonl --page-workers 2
```

Skip OCR for low-signal rendered pages (by PNG size in bytes):

```bash
PYTHONPATH=src python -m file_parser.phase2_ocr --input /path/to/input --phase1 output/phase1.jsonl --output output/phase2_ocr.jsonl --skip-low-signal-bytes 15000
```

Note: `page-workers` is capped based on CPU count and `--workers`, and a warning is printed if combined concurrency exceeds CPU count.

Deterministic output order (forces single worker):

```bash
PYTHONPATH=src python -m file_parser.phase2_ocr --input /path/to/input --phase1 output/phase1.jsonl --output output/phase2_ocr.jsonl --ordered
```

Write per-page text files instead of inline text:

```bash
PYTHONPATH=src python -m file_parser.phase2_ocr --input /path/to/input --phase1 output/phase1.jsonl --output output/phase2_ocr.jsonl --text-dir output/ocr_text
```

## Phase 3: Unified text extraction

Phase 3 extracts normalized text records and writes Zstandard-compressed JSONL shards by default.

```bash
PYTHONPATH=src python -m text_extraction.phase3_extract_text \
  --input /path/to/input \
  --phase1 output/phase1.jsonl \
  --phase2 output/phase2_ocr.jsonl \
  --output-dir output/text
```

Key options:

- `--shard-size N` controls documents per shard (default: `5000`)
- `--compression {zstd,gzip,none}` controls shard compression (default: `zstd`)
- `--zstd-level N` sets Zstandard compression level (default: `3`)
- `--resume` continues from existing shard output
- `--output` is supported as a deprecated alias for `--output-dir`
- `--resume` enforces a single shard compression mode per output directory

Output layout:

- shard files: `output/text/docs_0001.jsonl.zst`, `docs_0002.jsonl.zst`, ...
- manifest: `output/text/manifest.json` with:
  - `shard_size`
  - `shards` entries (`shard`, `start_index`, `end_index`, `doc_count`)

## Dependencies

- Phase 0: Python standard library only
- Phase 1: `pypdf` (see `requirements.txt`)
- Phase 3 default compression: `zstandard`

## Phase 6: SQLite loaders

Load one output type at a time:

```bash
PYTHONPATH=src python -m loaders.load_entities --input output/entities.jsonl --db output/store.sqlite
PYTHONPATH=src python -m loaders.load_events --input output/events.jsonl --db output/store.sqlite
PYTHONPATH=src python -m loaders.load_conversations --input output/conversations.jsonl --db output/store.sqlite
```

Load all three through the CLI:

```bash
PYTHONPATH=src python -m file_parser.cli load all \
  --entities-input output/entities.jsonl \
  --events-input output/events.jsonl \
  --conversations-input output/conversations.jsonl \
  --db output/store.sqlite
```

Reset and rebuild store tables before loading:

```bash
PYTHONPATH=src python -m file_parser.cli load all --overwrite
```

Inspect the database quickly:

```bash
sqlite3 output/store.sqlite "SELECT COUNT(*) FROM entities;"
sqlite3 output/store.sqlite "SELECT COUNT(*) FROM events;"
sqlite3 output/store.sqlite "SELECT COUNT(*) FROM conversations;"
sqlite3 output/store.sqlite "SELECT COUNT(*) FROM mentions;"
```

### Loader troubleshooting

- Malformed JSONL lines are skipped and counted under `json_decode_errors`.
- Records missing `file_id`, `chunk_id`, or list-shaped `items` are skipped as `invalid_record_shape`.
- Re-running loaders with the same inputs is idempotent (`INSERT OR IGNORE` on unique keys).

## Phase 7: Validation sanity checks

Run validation after chunking + LLM extraction:

```bash
PYTHONPATH=src python -m file_parser.cli validate \
  --chunks output/text/chunks.jsonl \
  --entities output/entities.jsonl \
  --events output/events.jsonl \
  --conversations output/conversations.jsonl \
  --phase3 output/text
```

Metrics reported:

- chunk entity yield: `% chunks with at least one entity item`
- chunk event yield: `% chunks with at least one event item`
- LLM invalid JSON rate: `% records with error=invalid_json`
- empty text page rate: `% Phase 3 pages where text is empty/whitespace`

`fileparse run` now includes `validate` in default steps:

```bash
PYTHONPATH=src python -m file_parser.cli run --input /path/to/input --output output
```

You can still override steps explicitly:

```bash
PYTHONPATH=src python -m file_parser.cli run --input /path/to/input --steps extract-text,chunk,llm,load
```
