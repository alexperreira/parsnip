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

## Phase 5 — LLM Extraction (MVP)

Create a new folder:

src/llm/

Add three scripts (initial minimal versions):

1) extract_entities.py  
2) extract_events.py  
3) extract_conversations.py  

Each script should:
- Read chunks.jsonl
- Call a locally hosted LLM (Ollama, llama.cpp, or vLLM — user choice)
- Output strict JSONL:
  - entities.jsonl
  - events.jsonl
  - conversations.jsonl

Every record **must include evidence**:

{
  "file_id": "...",
  "chunk_id": "...",
  "page_range": [x, y],
  "quote": "short supporting text",
  "confidence": float
}

---

## Phase 6 — Minimal Storage Layer

Create a lightweight local store (no UI yet):

Use SQLite with tables:
- entities
- events
- conversations
- mentions (entity -> chunk mapping)

Write small loaders that ingest the JSONL outputs into these tables.

---

## Phase 7 — Sanity Checks

Add a validation script that reports:
- % chunks yielding at least one entity
- % chunks yielding at least one event
- rate of invalid JSON from LLM
- rate of empty text pages

---

## Definition of Done for Immediate Phase

You are done when:
- Every PDF produces normalized text output.
- You can run chunking end-to-end on a subset of files.
- You can run LLM extraction on chunks and load results into SQLite.
