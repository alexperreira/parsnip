# L4 Plan: Timeline Stitching

This document is an implementation plan for `docs/TODO_LONG_TERM.md` item **L4 — Timeline Stitching**:

- Normalize dates into a global timeline.
- Handle relative dates ("last Tuesday").
- Build per-case and cross-case timelines.

This is a plan only. Do not implement until explicitly instructed.

## Goal

Given the current pipeline outputs (Phase 5 JSONL + Phase 6 SQLite), add a deterministic, fail-soft
timeline stitching stage that:

1) parses and normalizes event dates into a comparable global form, and
2) produces sortable timelines per case and across cases.

## Non-Goals

- No UI (timelines can be exported, but no viewer).
- No graph database (keep SQLite).
- No probabilistic date guessing; ambiguity must be represented explicitly (ranges/unknowns).
- No schema-breaking changes to existing Phase 5/6 outputs (additive only).

## Current Baseline (What Exists Today)

- Phase 5 event extraction (`src/llm/extract_events.py`) outputs per chunk:
  - `event` (string)
  - `date` (string; often not ISO; may be relative/ambiguous)
  - `quote`, `confidence`
- Phase 6 loaders store events to SQLite (`src/loaders/load_events.py`, `src/loaders/store.py`):
  - `events(event, date, confidence, file_id, chunk_id, page_start, page_end, quote)`
- Identity signals extraction/loader exists (`src/llm/extract_identity_signals.py`, `src/loaders/load_identity_signals.py`):
  - `identity_signals(... attribute IN {dob,address,case_id} ...)`

Implications:
- L4 must treat `events.date` as **untrusted raw text**.
- Case grouping can be driven by `identity_signals.attribute == "case_id"` (when available), otherwise a
  deterministic fallback is required.
- Relative date resolution requires a **reference date** (anchor) per file/chunk/event.

## Definitions

- **Event mention**: one row in `events` (tied to `file_id`, `chunk_id`, `page_start/page_end`).
- **Raw date**: the extracted `events.date` string.
- **Normalized date range**: `[date_start, date_end]` in ISO `YYYY-MM-DD`, plus a `precision` label.
  - Examples:
    - `2024-01-05` exact day => `date_start=date_end=2024-01-05`, `precision=day`
    - `Jan 2024` => `date_start=2024-01-01`, `date_end=2024-01-31`, `precision=month`
    - `2024` => `date_start=2024-01-01`, `date_end=2024-12-31`, `precision=year`
- **Anchor date**: a reference `YYYY-MM-DD` used to resolve relative expressions.
- **Case**: an identifier (prefer `case_id`); used to group timelines.

## Design Decisions (Lock Before Coding)

- Determinism:
  - Same inputs + same config => same normalized outputs.
  - Relative-date rules are fixed and documented (no “best guess” heuristics).
- Fail-soft:
  - Malformed dates or missing anchors do not crash the run; affected events are marked unresolved.
- Privacy / data leakage:
  - Console logs print counts and codes only; no raw `event`, `quote`, file paths, or extracted date strings.
- Additive schema evolution:
  - Keep `events` intact; add new tables for normalization output and indexing.
- Ambiguity is first-class:
  - If a date cannot be resolved safely, store `status=unresolved_*` and keep the raw string.

## Data Contracts (Additive)

### A) New SQLite tables (schema v4+)

1) `files` (optional but recommended; anchors + provenance)

Loaded from Phase 0 `manifest.jsonl` by `file_id`.

- `file_id TEXT PRIMARY KEY`
- `source_type TEXT`
- `container_path TEXT`
- `virtual_path TEXT`
- `mtime_utc TEXT` (ISO-8601; from manifest)
- `size_bytes INTEGER`

2) `event_times` (normalized date range per event)

- `event_id INTEGER PRIMARY KEY` (matches `events.event_id`)
- `date_raw TEXT` (copied from `events.date`)
- `date_start TEXT` (ISO `YYYY-MM-DD`, nullable)
- `date_end TEXT` (ISO `YYYY-MM-DD`, nullable)
- `precision TEXT` (`day|month|year|range|unknown`)
- `status TEXT` (`ok|unresolved_relative|unresolved_ambiguous|invalid_format|missing_anchor|empty`)
- `parser TEXT` (e.g. `absolute_v1|relative_v1`)
- `anchor_date TEXT` (ISO `YYYY-MM-DD`, nullable; only for relative resolutions)
- `notes_json TEXT` (small JSON for reason codes; no raw content)

3) `event_cases` (many-to-many event↔case links)

- `event_id INTEGER NOT NULL`
- `case_id TEXT NOT NULL`
- `case_id_norm TEXT NOT NULL`
- `source TEXT NOT NULL` (`identity_signals|fallback`)
- `PRIMARY KEY(event_id, case_id_norm, source)`

4) (Optional) `case_timelines` export table/view

Not strictly needed if consumers query `events` + `event_times` + `event_cases`, but can be useful for
materialized summaries:

- `case_id_norm TEXT NOT NULL`
- `event_id INTEGER NOT NULL`
- `sort_date TEXT` (ISO; e.g. `date_start` or a deterministic fallback)
- `PRIMARY KEY(case_id_norm, event_id)`

Indexes:
- `CREATE INDEX idx_event_times_start ON event_times(date_start);`
- `CREATE INDEX idx_event_cases_case ON event_cases(case_id_norm);`

Notes:
- Prefer keying normalization on `event_id` to avoid duplication and keep joins stable.
- Use `INSERT OR REPLACE` for `event_times` keyed by `event_id` to make runs idempotent.

### B) New command (Phase 9-style): timeline stitching

Add a new module (proposed): `src/timeline/phase9_stitch_timeline.py` with:

- Inputs: `--db output/store.sqlite` and optionally `--manifest output/manifest.jsonl`
- Output: new tables in the same SQLite DB (preferred for joinability)
- Optional exports:
  - `--export-jsonl output/timeline.jsonl` (records contain only IDs + normalized dates by default)

CLI integration (proposed):
- `fileparse timeline -- --db output/store.sqlite --manifest output/manifest.jsonl`
- Add `timeline` as an optional `fileparse run --steps ...` stage after `load` (and after `resolve` if used).

## Anchor Strategy (Deterministic Priority Order)

Relative date resolution needs an anchor date per event mention. Use the first available source in this order:

1) **Chunk anchor (explicit absolute date in same chunk)**:
   - deterministically scan the chunk text for strict absolute date patterns (see parser section).
   - choose the earliest absolute date occurrence in the chunk as `anchor_date`.
2) **File anchor (manifest mtime)**:
   - if `files.mtime_utc` exists, use its UTC calendar date as `anchor_date`.
3) **No anchor**:
   - mark `status=missing_anchor` and leave `date_start/date_end` null.

Rationale:
- Chunk anchors reflect the narrative “as-of” date more accurately than filesystem metadata when available.
- Manifest mtime is cheap and always present for most sources; it is a deterministic fallback.

## Date Parsing & Normalization

### 1) Absolute date parsing (stdlib-only, strict)

Support a conservative set of patterns first (expand only with tests):

- ISO day: `YYYY-MM-DD`
- ISO month: `YYYY-MM`
- US common: `MM/DD/YYYY` (reject `MM/DD/YY` to avoid guessing centuries)
- Month name: `Jan 2, 2024`, `2 Jan 2024`, `January 2024`
- Year-only: `YYYY`
- Ranges:
  - `YYYY-MM-DD to YYYY-MM-DD`
  - `Jan 2–5, 2024` (normalize to start/end within same month)

Normalization rules:
- Produce `date_start/date_end` and `precision`.
- If the input contains a time-of-day, ignore it for now (timeline is date-level).
- If multiple absolute dates appear in `date_raw`:
  - if it looks like a range => normalize to a range,
  - else mark `status=unresolved_ambiguous`.

### 2) Relative date parsing (anchored, strict)

Recognize a limited set of relative forms and resolve deterministically:

- `today|yesterday|tomorrow`
- `N days|weeks|months|years ago`
- `in N days|weeks|months|years`
- `last|next|this <weekday>` (Mon..Sun)

Rules to lock in (examples assume `anchor_date = 2026-02-20` (Friday)):

- `last Tuesday` => strictly before anchor: `2026-02-17`
- `next Tuesday` => strictly after anchor: `2026-02-24`
- `this Tuesday` => the next occurrence on/after anchor within 0..6 days:
  - from Friday 2026-02-20 => `2026-02-24`
- If the relative expression implies a range (`in the last 3 weeks`, `over the past month`), normalize
  to a date range when the phrasing is unambiguous; otherwise mark ambiguous.

If parsing succeeds but resolution would overflow bounds (unlikely) or produce invalid dates:
- mark `status=invalid_format`.

If parsing succeeds but the anchor is missing:
- mark `status=missing_anchor`.

## Case Grouping (Per-case timelines)

Primary strategy:
- For each event mention (`events.file_id`, `events.chunk_id`), attach case IDs from
  `identity_signals` where `attribute == "case_id"` for the same `file_id/chunk_id`.
  - Normalize case IDs (`case_id_norm`) using a deterministic canonicalization:
    - trim, lowercase, collapse whitespace, remove obviously decorative punctuation.

Fallback strategy (when no `case_id` exists for the chunk):
- Use a deterministic surrogate case key:
  - `case_id_norm = "file:" + file_id` and `source=fallback`.

Notes:
- If multiple case IDs are present for a chunk, link the event to all of them (many-to-many).
- Do not attempt cross-file “case resolution” without explicit signals (keep it conservative).

## Cross-case Timeline (Global view)

A “cross-case” timeline is the same normalized event set sorted globally, with case links preserved.

Minimum viable output:
- Query all events joined to `event_times` sorted by `date_start`, then by `(file_id, chunk_id, event_id)`
  for stable tie-breaking.

Optional enrichment (later, still deterministic):
- Attach `person_id` participation using co-occurrence:
  - join `mentions` / `entities` rows (and `person_clusters` if Phase 8 is run) by `(file_id, chunk_id)`.
  - store in an additive table `event_participants(event_id, person_id|entity_text, source)`.

## Observability (Without Data Leakage)

Timeline stitcher prints a summary like:

- events_total
- events_with_nonempty_date_raw
- normalized_ok
- unresolved_relative
- missing_anchor
- invalid_format
- unresolved_ambiguous
- case_links_total (by source)
- elapsed_seconds by stage (load inputs, normalize, write tables)

Include at most K redacted samples:
- hash of `date_raw` (e.g., sha256 first 8 chars) + status code (no raw strings).

## Testing Plan

- [x] Add unit tests (pure functions) for:

  - [x] absolute parsing (each supported format; ranges; ambiguity)
  - [x] relative parsing/resolution (fixed anchor dates; all weekdays; boundary behavior)
  - [x] case-id normalization

- [x] Add integration tests using a temporary SQLite DB:
  - [x] insert a few `events` rows + `identity_signals` + optional `files` rows
  - [x] run the stitcher
  - [x] assert `event_times` and `event_cases` contents are stable and correct

## Codex-Ready TODO Checklist (When Implementing)

- [x] Add schema v4 tables in `src/loaders/store.py` (additive).
- [x] Add a `manifest` loader (optional) to populate `files`.
- [x] Implement a strict date parser module (stdlib-only) with exhaustive tests.
- [x] Implement the stitcher (`phase9_stitch_timeline`) that:
  - [x] reads `events` (and optionally chunk text/manifest for anchors),
  - [x] writes `event_times` + `event_cases`,
  - [x] prints redacted summaries.
- [x] Add CLI wiring (`src/file_parser/cli.py`) and documentation updates in `README.md`.
