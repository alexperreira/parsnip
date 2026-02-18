# Zstandard (`.zst`) As Default Shard Format (Implementation Plan)

This document is an implementation checklist to make Phase 3 shard outputs default to
Zstandard-compressed JSONL (`.jsonl.zst`) instead of gzip (`.jsonl.gz`).

The plan is intentionally "robust": it includes explicit CLI flags, backwards-compatible
readers, clear docs, and tests to prevent regressions.

## Goal

- Default Phase 3 shard outputs: `docs_0001.jsonl.zst`, `docs_0002.jsonl.zst`, ...
- Keep read support for existing shard formats:
  - `docs_*.jsonl.gz`
  - `docs_*.jsonl`
- Keep determinism: file extension controls compression behavior (no content sniffing).

## Non-Goals

- Parallel sharding / distributed processing / dashboards (these are separate `L1` items).
- Changing JSON schemas for Phase 3, Phase 4, Phase 5, Phase 6, Phase 7 outputs.
- Re-compressing existing historical outputs automatically.

## Why `.zst`

- Smaller shard sizes (often materially better ratio than gzip).
- Often faster decompression than gzip (reduces I/O wall time when re-reading shards).
- Streaming-friendly (important for large JSONL).

## Design Decisions (Lock These Before Coding)

### 1) Compression implementation

- Use the Python `zstandard` library for streaming I/O.
- Do not shell out to `zstd` by default (reduces environment variability).

### 2) Backwards compatibility and discovery

- Prefer `manifest.json` when present (source of truth for shard names).
- When no manifest exists, discover shards by globbing in this order:
  1. `docs_*.jsonl.zst` (new default)
  2. `docs_*.jsonl.gz`
  3. `docs_*.jsonl`

### 3) CLI behavior (robust)

- Add `--compression {zstd,gzip,none}` to Phase 3, default `zstd`.
- Add optional `--zstd-level N` (document the default).
- Keep `.gz` readable forever, but do not write `.gz` by default.

### 4) Determinism and safety

- Suffix-based behavior only:
  - `.zst` implies zstd
  - `.gz` implies gzip
  - otherwise plain text
- If `.zst` is requested but `zstandard` is missing:
  - fail fast with a clear error message
  - do not silently downgrade to gzip

## Code Changes (By Component)

### A) Add a shared compression I/O helper (new module)

Create a small helper to centralize all compression logic. Suggested API:

- `open_text_reader(path: Path) -> TextIO`
- `open_text_writer(path: Path) -> TextIO`

Routing rules:

- `path.suffix == ".zst"` uses `zstandard`
- `path.suffix == ".gz"` uses `gzip`
- else uses `Path.open(...)`

Notes:

- Keep the helper small and dependency-free except for optional `zstandard` import.
- Provide a single, consistent error message for missing `zstandard`.

Where to place it:

- Prefer `src/file_parser/compress_io.py` because Phase 4 and Phase 7 live outside
  `src/text_extraction/`, and this keeps the helper in a "shared" namespace.

### B) Phase 3 writer: default `.zst` shards

Update `src/text_extraction/phase3_extract_text.py`:

- Add CLI flags:
  - `--compression {zstd,gzip,none}` default `zstd`
  - `--zstd-level N` (optional)
- Change shard naming based on `--compression`:
  - `zstd`: `docs_0001.jsonl.zst`
  - `gzip`: `docs_0001.jsonl.gz`
  - `none`: `docs_0001.jsonl`
- Write shards via the shared helper so I/O logic is consistent.
- Ensure `output/text/manifest.json` records the correct shard filenames.

Resume considerations:

- Ensure `--resume` continues to work regardless of shard suffix.
- The resume DB should track logical work (e.g. `file_id`) rather than shard filenames.

### C) Phase 4 reader: accept `.zst` shards

Update `src/chunking/phase4_chunk.py`:

- Replace current gzip-only opener with the shared helper.
- Update shard discovery fallback globbing to include `.zst` (and prefer it).
- Keep behavior deterministic (no auto-detecting compression beyond filename).
- Ensure errors mention supported suffixes (`.zst`, `.gz`, `.jsonl`).

### D) Phase 7 reader: accept `.zst` shards

Update `src/file_parser/phase7_validate.py`:

- Replace current gzip-only opener with the shared helper.
- Update fallback shard discovery to include `.zst` if manifest is missing.

## Dependency Changes

Add `zstandard` to both:

- `pyproject.toml` (project dependency)
- `requirements.txt`

Rationale:

- If `.zst` is the default output, the dependency must be installed for the default
  pipeline to function.

## Documentation Updates

Update `README.md`:

- Phase 3 output layout should show `.jsonl.zst` shards by default.
- Document `--compression gzip` to keep `.gz` outputs when desired.
- Document any new flags (`--zstd-level`).

Update `Makefile` (optional but recommended):

- Keep `make phase3` working with defaults.
- Consider a variable:
  - `PHASE3_COMPRESSION ?= zstd`
  - and pass `--compression $(PHASE3_COMPRESSION)` to Phase 3.

## Tests

Add/extend tests to cover:

### 1) `.zst` round-trip read support

- Create a tiny shard (1-2 docs) in `.jsonl.zst`.
- Ensure Phase 4 can read it and produce chunks.
- Ensure Phase 7 can read it and compute counts.

### 2) Backwards compatibility

- Keep at least one test that reads `.jsonl.gz` shards (or writes them) to ensure
  gzip support does not regress.

Testing approach recommendation:

- Prefer using the shared helper to write small test shards deterministically rather
  than mocking internals.

## Acceptance Criteria

- Phase 3 defaults to writing `.jsonl.zst` shards and `manifest.json` references them.
- Phase 4 chunking works when `--input` points at a directory with `.zst` shards.
- Phase 7 validation works when `--phase3` points at a directory with `.zst` shards.
- `.jsonl.gz` and plain `.jsonl` shard directories remain readable by Phase 4 and Phase 7.
- `make test` passes in a clean environment with declared Python deps installed.

## Rollout / Migration Notes

- Existing outputs are not auto-migrated. If you have historical `output/text/*.gz` shards,
  they remain readable.
- Consider documenting a simple one-off migration command (optional) if disk pressure is
  a concern, but keep it out of the core pipeline.

## Commands To Run During Implementation (When Approved)

Search for gzip assumptions and shard suffixes:

- `rg -n "docs_\\*\\.jsonl\\.gz|\\.suffix == \"\\.gz\"|gzip\\.open|docs_\\*\\.jsonl" -S src tests README.md Makefile`

Run tests:

- `make test`

Optional smoke run (requires a small input directory with PDFs):

- `PYTHONPATH=src python -m file_parser.run_pipeline --input /path/to/input --output-dir output`
- `PYTHONPATH=src python -m text_extraction.phase3_extract_text --input /path/to/input --phase1 output/phase1.jsonl --phase2 output/phase2_ocr.jsonl --output-dir output/text`
- `PYTHONPATH=src python -m chunking.phase4_chunk --input output/text --output output/text/chunks.jsonl`
- `PYTHONPATH=src python -m file_parser.phase7_validate --chunks output/text/chunks.jsonl --entities output/entities.jsonl --events output/events.jsonl --conversations output/conversations.jsonl --phase3 output/text`

