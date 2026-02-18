# TODO: Immediate (Codex Execution Plan)

This file defines the *next executable milestones* for the Parsnip pipeline.  
Work through items **in order** unless explicitly told otherwise.

---

## Phase 3 — Unified Text Extraction (PRIORITY)

---

## Completed

### 3.3 Sharded + compressed outputs

- Sharded output uses gzip-compressed JSONL files (`docs_0001.jsonl.gz`, etc.).
- Shard size is configurable via `--shard-size` (default 5000).
- `output/text/manifest.json` stores an object with `shard_size` and `shards`.
- Deprecated `--output` is supported as an alias for `--output-dir`.
- Make targets for Phase 3 and cleanup are available.

## Phase 4 — Chunking for Analysis

- Added `src/chunking/phase4_chunk.py` to generate `chunks.jsonl` from Phase 3 output.
- Default chunk size is 2 pages with 1-page overlap; dialogue pages force single-page chunks.
- Supports `--overwrite`, `--append` (SQLite-backed dedupe), and `--replace-file-ids`.

## Phase 5 — LLM Extraction (MVP)

- Added `src/llm/` with `extract_entities.py`, `extract_events.py`, `extract_conversations.py`.
- Reads `chunks.jsonl`, calls a local LLM (Ollama by default), and writes strict JSONL outputs.
- Output records include:
  - `file_id`, `chunk_id`, `page_range`
  - `items` list with per-item evidence: `quote` + `confidence`.
- Supports `--signals` (defaults to `llama3.1:8b`) and `--narrative` (defaults to `qwen2.5:32b`),
  with `--model` as an override.

## Phase 6 — Minimal Storage Layer

- Added shared SQLite storage module: `src/loaders/store.py`.
- Canonical schema is now centralized for:
  - `entities`, `events`, `conversations`, `mentions`, `meta`
- Loader behavior is deterministic and fail-soft:
  - input envelope + item validation
  - normalized `page_range` -> `page_start/page_end`
  - idempotent reruns via unique keys + `INSERT OR IGNORE`
  - normalized quote whitespace for stronger dedupe
  - consistent summary counters across loaders
- CLI integration completed:
  - `fileparse load all` added
  - `fileparse run --steps ...` load stage uses shared loader path
- Coverage added:
  - `tests/test_phase6_loaders.py`
  - `tests/test_cli_load_help.py`
- README updated with Phase 6 usage and troubleshooting.

## Phase 7 — Sanity Checks

- Added `src/file_parser/phase7_validate.py` with deterministic validation metrics:
  - % chunks yielding at least one entity
  - % chunks yielding at least one event
  - rate of invalid JSON from LLM outputs
  - rate of empty text pages from Phase 3 shards
- Added integrity warnings for chunk/output count mismatches:
  - warns when entities record count differs from total chunk count
  - warns when events record count differs from total chunk count
- Added `fileparse validate` command wiring in `src/file_parser/cli.py`.
- Updated `fileparse run` default steps to include `validate`.
- Added coverage:
  - `tests/test_phase7_validate.py`
  - `tests/test_cli_validate_help.py`

### 3.4 Resume behavior

phase3_extract_text.py must:
- Support --resume
- Track processed file_ids in a SQLite DB (similar to earlier phases).
- Be restart-safe.

### Phase 3.1 — Create module
- Added `src/text_extraction/phase3_extract_text.py` with PDF text extraction for `text` classifications.
- Uses Phase 2 OCR outputs for `scanned|mixed|unknown`.
- Reads OCR `text_path` when `--text-dir` is used.
- Labels pages with `review_required` + `review_reason` if OCR `text_path` is missing/unreadable.

Notes:
- Original Phase 3.1 output shape has been superseded by Phase 3.3 sharded output.

### Phase 3.2 — Define canonical document schema
- Added `review_required` + `review_reason` to PDF-text pages so all page records match the canonical schema.
- `quality_score` now combines non-empty text ratio with OCR confidence (when available).

Notes:
- OCR confidence is assumed to be in [0,1]; values above 1 are clamped to 1.0 before averaging.

---

## Deferred follow-ups

- Improve CLI passthrough help for `fileparse validate` so validator-specific flags appear in command help output.
- Consider an alternate invalid JSON rate denominator that includes malformed JSONL lines as an explicit mode.

---

## Definition of Done for Immediate Phase

You are done when:
- Every PDF produces normalized text output.
- You can run chunking end-to-end on a subset of files.
- You can run LLM extraction on chunks and load results into SQLite.
