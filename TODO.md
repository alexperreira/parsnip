# TODO: Immediate (Codex Execution Plan)

This file defines the *next executable milestones* for the Parsnip pipeline.  
Work through items **in order** unless explicitly told otherwise.

---

## Phase 3 — Unified Text Extraction (PRIORITY)

---

## Completed

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

### 3.4 Resume behavior

phase3_extract_text.py must:
- Support --resume
- Track processed file_ids in a SQLite DB (similar to earlier phases).
- Be restart-safe.

### 3.3 Sharded + compressed outputs

Notes:
- Sharded output uses gzip-compressed JSONL files (`docs_0001.jsonl.gz`, etc.).
- Shard size is configurable via `--shard-size` (default 5000).
- `output/text/manifest.json` now stores an object with `shard_size` and `shards`.
- Deprecated `--output` is supported as an alias for `--output-dir`.
- Added Make targets for phases and cleanup; documented in README.

### Phase 3.1 — Create module
- Added `src/text_extraction/phase3_extract_text.py` with PDF text extraction for `text` classifications.
- Uses Phase 2 OCR outputs for `scanned|mixed|unknown`.
- Reads OCR `text_path` when `--text-dir` is used.
- Labels pages with `review_required` + `review_reason` if OCR `text_path` is missing/unreadable.

Notes:
- Current Phase 3 output is a single JSONL file (no sharding yet).
- No resume support yet.

### Phase 3.2 — Define canonical document schema
- Added `review_required` + `review_reason` to PDF-text pages so all page records match the canonical schema.
- `quality_score` now combines non-empty text ratio with OCR confidence (when available).

Notes:
- OCR confidence is assumed to be in [0,1]; values above 1 are clamped to 1.0 before averaging.

## Phase 6 — Minimal Storage Layer

Goal: harden the existing SQLite MVP loaders into a deterministic, testable Phase 6 baseline.

### 6.1 Schema contract + migration safety
- [ ] Define canonical SQLite schema in one shared module (single source of truth):
  - tables: `entities`, `events`, `conversations`, `mentions`
  - required columns, types, and minimal constraints (`NOT NULL` where safe)
  - indexes for common lookups (`file_id`, `chunk_id`, `entity`, `date`)
- [ ] Add `meta` table with `schema_version` and loader run timestamps.
- [ ] Make `--overwrite` deterministic across all loaders (same drop/create order).

### 6.2 Idempotent + deterministic loading behavior
- [ ] Add stable natural keys and UPSERT rules to prevent duplicate inserts on reruns.
- [ ] Normalize `page_range` mapping (`page_start`, `page_end`) consistently across loaders.
- [ ] Track parse/load stats uniformly:
  - rows attempted
  - rows inserted
  - rows skipped
  - JSON decode errors
  - invalid item-shape errors
- [ ] Fail-soft contract: continue past bad lines; summarize failures at end.

### 6.3 Input validation + redaction-safe observability
- [ ] Validate expected record envelope (`file_id`, `chunk_id`, `items`) before item ingest.
- [ ] Validate per-item required fields by table (`entity`, `event`, `speaker` as applicable).
- [ ] Ensure loader logs never print full raw input lines; only counters and short redacted samples.

### 6.4 CLI integration and run-pipeline behavior
- [ ] Add explicit `fileparse load all` command to run entities/events/conversations loaders in order.
- [ ] Wire `fileparse run --steps ...` load stage to shared loader utilities.
- [ ] Document loader defaults (`--db`, `--overwrite`, expected JSONL paths) in CLI help.

### 6.5 Tests (required for Phase 6 exit)
- [ ] Add loader unit tests for:
  - schema creation
  - JSONL line parsing + error handling
  - `page_range` mapping
  - idempotent rerun behavior
- [ ] Add one integration test that loads all three JSONLs into one SQLite DB and verifies:
  - row counts
  - index presence
  - mention linkage integrity

### 6.6 Documentation + handoff
- [ ] Update README with Phase 6 usage:
  - single loaders (`fileparse load entities|events|conversations`)
  - combined load (`fileparse load all`)
  - inspect DB examples (`sqlite3 output/store.sqlite ...`)
- [ ] Add a short troubleshooting section for malformed JSONL and duplicate runs.

### Phase 6 Definition of Done
- [ ] Re-running loaders on the same inputs does not create duplicate rows.
- [ ] All three loader outputs can be ingested into one SQLite file with consistent schema.
- [ ] `pytest` includes Phase 6 tests and they pass locally.
- [ ] `fileparse run --steps extract-text,chunk,llm,load` completes without manual DB fixes.

---

## Phase 7 — Sanity Checks

Add a validation script that reports:
- % chunks yielding at least one entity
- % chunks yielding at least one event
- rate of invalid JSON from LLM
- rate of empty text pages

Note: `fileparse run` currently skips `validate` by default; update default steps when Phase 7 exists.

---

## Definition of Done for Immediate Phase

You are done when:
- Every PDF produces normalized text output.
- You can run chunking end-to-end on a subset of files.
- You can run LLM extraction on chunks and load results into SQLite.
