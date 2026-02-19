# Zstandard (`.zst`) As Default Shard Format (Implementation Plan)

This document is an implementation checklist to make Phase 3 shard outputs default to
Zstandard-compressed JSONL (`.jsonl.zst`) instead of gzip (`.jsonl.gz`).

The plan is intentionally "robust": it includes explicit CLI flags, backwards-compatible
readers, clear docs, and tests to prevent regressions.

## Goal

- [x] Default Phase 3 shard outputs: `docs_0001.jsonl.zst`, `docs_0002.jsonl.zst`, ...
- [x] Keep read support for existing shard formats:
  - `docs_*.jsonl.gz`
  - `docs_*.jsonl`
- [x] Keep determinism: file extension controls compression behavior (no content sniffing).

## Non-Goals

- [ ] Parallel sharding / distributed processing / dashboards (these are separate `L1` items).
- [ ] Changing JSON schemas for Phase 3, Phase 4, Phase 5, Phase 6, Phase 7 outputs.
- [ ] Re-compressing existing historical outputs automatically.

## Why `.zst`

- [ ] Smaller shard sizes (often materially better ratio than gzip).
- [ ] Often faster decompression than gzip (reduces I/O wall time when re-reading shards).
- [ ] Streaming-friendly (important for large JSONL).

## Design Decisions (Lock These Before Coding)

### 1) Compression implementation

- [x] Use the Python `zstandard` library for streaming I/O.
- [x] Do not shell out to `zstd` by default (reduces environment variability).

### 2) Backwards compatibility and discovery

- [x] Prefer `manifest.json` when present (source of truth for shard names).
- [x] When no manifest exists, discover shards by globbing in this order:
  1. `docs_*.jsonl.zst` (new default)
  2. `docs_*.jsonl.gz`
  3. `docs_*.jsonl`

### 3) CLI behavior (robust)

- [x] Add `--compression {zstd,gzip,none}` to Phase 3, default `zstd`.
- [x] Add optional `--zstd-level N` (document the default).
- [x] Keep `.gz` readable forever, but do not write `.gz` by default.

### 4) Determinism and safety

- [x] Suffix-based behavior only:
  - `.zst` implies zstd
  - `.gz` implies gzip
  - otherwise plain text
- [x] If `.zst` is requested but `zstandard` is missing:
  - fail fast with a clear error message
  - do not silently downgrade to gzip

## Code Changes (By Component)

### A) Add a shared compression I/O helper (new module)

Create a small helper to centralize all compression logic. Suggested API:

- [x] `open_text_reader(path: Path) -> TextIO`
- [x] `open_text_writer(path: Path, zstd_level: int | None = None) -> TextIO`

Routing rules:

- [x] `path.suffix == ".zst"` uses `zstandard`
- [x] `path.suffix == ".gz"` uses `gzip`
- [x] else uses `Path.open(...)`

Notes:

- [x] Keep the helper small and dependency-free except for optional `zstandard` import.
- [x] Provide a single, consistent error message for missing `zstandard`.

Where to place it:

- [x] Prefer `src/file_parser/compress_io.py` because Phase 4 and Phase 7 live outside
  `src/text_extraction/`, and this keeps the helper in a "shared" namespace.

### B) Phase 3 writer: default `.zst` shards

Update `src/text_extraction/phase3_extract_text.py`:

- [x] Add CLI flags:
  - `--compression {zstd,gzip,none}` default `zstd`
  - `--zstd-level N` (optional)
- [x] Change shard naming based on `--compression`:
  - `zstd`: `docs_0001.jsonl.zst`
  - `gzip`: `docs_0001.jsonl.gz`
  - `none`: `docs_0001.jsonl`
- [x] Write shards via the shared helper so I/O logic is consistent.
- [x] Ensure `output/text/manifest.json` records the correct shard filenames.

Resume considerations:

- [x] Ensure `--resume` continues to work regardless of shard suffix.
- [x] The resume DB should track logical work (e.g. `file_id`) rather than shard filenames.
- [x] Add an explicit compression compatibility check for resume:
  - if existing shards/manifest are `gzip` or `none`, and user requests `zstd`, fail fast
  - do not silently mix shard formats in one output directory
- [x] Update all Phase 3 shard indexing/discovery paths to support `.jsonl.zst`, `.jsonl.gz`,
  and `.jsonl` (manifest parsing, fallback shard globbing, shard index parsing).

### C) Phase 4 reader: accept `.zst` shards

Update `src/chunking/phase4_chunk.py`:

- [x] Replace current gzip-only opener with the shared helper.
- [x] Update shard discovery fallback globbing to include `.zst` (and prefer it).
- [x] Keep behavior deterministic (no auto-detecting compression beyond filename).
- [x] Ensure errors mention supported suffixes (`.zst`, `.gz`, `.jsonl`).

### D) Phase 7 reader: accept `.zst` shards

Update `src/file_parser/phase7_validate.py`:

- [x] Replace current gzip-only opener with the shared helper.
- [x] Update fallback shard discovery to include `.zst` if manifest is missing.

## Dependency Changes

Add `zstandard` to both:

- [x] `pyproject.toml` (project dependency)
- [x] `requirements.txt`

Rationale:

- [x] If `.zst` is the default output, the dependency must be installed for the default
  pipeline to function.

## Documentation Updates

Update `README.md`:

- [x] Phase 3 output layout should show `.jsonl.zst` shards by default.
- [x] Document `--compression gzip` to keep `.gz` outputs when desired.
- [x] Document any new flags (`--zstd-level`).

Update `Makefile` (optional but recommended):

- [x] Keep `make phase3` working with defaults.
- [x] Consider a variable:
  - `PHASE3_COMPRESSION ?= zstd`
  - and pass `--compression $(PHASE3_COMPRESSION)` to Phase 3.

## Tests

Add/extend tests to cover:

### 1) `.zst` round-trip read support

- [x] Create a tiny shard (1-2 docs) in `.jsonl.zst`.
- [x] Ensure Phase 4 can read it and produce chunks.
- [x] Ensure Phase 7 can read it and compute counts.

### 2) Backwards compatibility

- [x] Keep at least one test that reads `.jsonl.gz` shards (or writes them) to ensure
  gzip support does not regress.

### 3) Phase 3 behavior

- [x] Verify Phase 3 default output shard names are `.jsonl.zst`.
- [x] Verify `--resume` fails fast on compression mismatch (prevents mixed-format outputs).
- [x] Verify clear failure when `.zst` is requested and `zstandard` dependency is unavailable.

Testing approach recommendation:

- [x] Prefer using the shared helper to write small test shards deterministically rather
  than mocking internals.

## Acceptance Criteria

- [x] Phase 3 defaults to writing `.jsonl.zst` shards and `manifest.json` references them.
- [x] Phase 4 chunking works when `--input` points at a directory with `.zst` shards.
- [x] Phase 7 validation works when `--phase3` points at a directory with `.zst` shards.
- [x] `.jsonl.gz` and plain `.jsonl` shard directories remain readable by Phase 4 and Phase 7.
- [x] Resume mode enforces single compression mode per output directory (no mixed shard suffixes).
- [x] Requesting `.zst` without `zstandard` installed fails with a clear, actionable error.
- [x] `make test` passes in a clean environment with declared Python deps installed.

## Rollout / Migration Notes

- [x] Existing outputs are not auto-migrated. If you have historical `output/text/*.gz` shards,
  they remain readable.
- [ ] Consider documenting a simple one-off migration command (optional) if disk pressure is
  a concern, but keep it out of the core pipeline.

## Operational Notes (Day-to-Day Use)

- [ ] Quick inspection of shard contents will change from `zcat` to `zstdcat` (or `unzstd -c`).
- [ ] Any ad-hoc scripts that glob for `docs_*.jsonl.gz` must be updated to prefer:
  - `docs_*.jsonl.zst` first, then `.gz`, then `.jsonl`
- [ ] For a one-time ingest, the primary benefit is disk and transfer size reduction; the
  largest wall-time wins will still typically come from OCR and LLM stages.

## Follow-On Work (Explicitly Out of Scope Here)

This plan only changes the shard compression format. It does not address mixed-PDF
quality/cost issues.

- [ ] `L2 — Smarter Mixed-PDF Handling` (recommended next if you expect many `mixed` PDFs):
  - Add deterministic per-page decisions for `mixed` PDFs: use embedded text where present,
    OCR only image-heavy / low-text pages.
  - Add per-page quality scoring fields in Phase 3 page records.
  - Phase 2 and Phase 3 will need schema-aware changes, but these should remain orthogonal
    to shard compression (the `.zst` helper should be reusable as-is).

## Commands To Run During Implementation (When Approved)

Search for gzip assumptions and shard suffixes:

- [x] `rg -n "docs_\\*\\.jsonl\\.gz|\\.suffix == \"\\.gz\"|gzip\\.open|docs_\\*\\.jsonl" -S src tests README.md Makefile`

Run tests:

- [x] `make test`

Optional smoke run (requires a small input directory with PDFs):

- [ ] `PYTHONPATH=src python -m file_parser.run_pipeline --input /path/to/input --output-dir output`
- [ ] `PYTHONPATH=src python -m text_extraction.phase3_extract_text --input /path/to/input --phase1 output/phase1.jsonl --phase2 output/phase2_ocr.jsonl --output-dir output/text`
- [ ] `PYTHONPATH=src python -m chunking.phase4_chunk --input output/text --output output/text/chunks.jsonl`
- [ ] `PYTHONPATH=src python -m file_parser.phase7_validate --chunks output/text/chunks.jsonl --entities output/entities.jsonl --events output/events.jsonl --conversations output/conversations.jsonl --phase3 output/text`
