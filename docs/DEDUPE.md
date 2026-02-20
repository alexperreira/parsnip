# L3 Plan: Entity Resolution (Deduplication)

This document is an implementation plan for `docs/TODO_LONG_TERM.md` item **L3 — Entity Resolution (Deduplication)**:

- Merge duplicate people across files.
- Handle aliases (e.g., "Robert Smith" vs "Bob Smith").
- Link identities via shared attributes (DOB, case ID, address).

This is a plan only. Do not implement until explicitly instructed.

## Goal

Given the current pipeline outputs (Phase 5 JSONL + Phase 6 SQLite), add a deterministic, fail-soft
entity-resolution stage that produces **canonical people** and links downstream records to them.

## Non-Goals

- No graph database (keep SQLite).
- No UI.
- No probabilistic/opaque auto-merging; decisions must be explainable and reproducible.
- No schema-breaking changes to existing Phase 5/6 outputs (additive only).

## Current Baseline (What Exists Today)

- Phase 5 entity extraction (`src/llm/extract_entities.py`) outputs per chunk:
  - `entity`, `type`, `quote`, `confidence` (no DOB/address/case ID fields).
- Phase 6 loaders store to SQLite (`src/loaders/store.py`):
  - `entities(entity, type, confidence, file_id, chunk_id, page_start, page_end, quote)`
  - `mentions(entity, file_id, chunk_id)` (one per chunk per entity string)

Implication: L3 needs either:
- a new structured "identity signals" extraction step, or
- a deterministic post-processor that derives signals from existing text (likely insufficient for DOB/address).

This plan assumes we add a small **identity signal extraction** step (LLM or rules), then resolve people
in SQLite using deterministic scoring and clustering.

## Definitions

- **Observation**: A single extracted mention of a person in a specific file/chunk (backed by existing
  `entities`/`mentions` rows).
- **Identity signal**: A structured attribute associated with an observation (DOB, address, case ID, etc).
- **Cluster**: A canonical person, represented as a set of observations believed to be the same individual.
- **Decision**: `auto_merge` / `needs_review` / `no_merge` between candidate pairs, with an explainable reason.

## Design Decisions (Lock Before Coding)

- Determinism:
  - Candidate generation and scoring is rule-based only.
  - Same input DB + same config => same outputs.
- Fail-soft:
  - Bad rows or malformed signal records are skipped and counted; one bad file/chunk must not crash the run.
- Privacy:
  - Console logs must not print raw names/addresses/DOBs; log only counts + redacted samples (hash/truncate).
- Conservative merging:
  - Prefer false negatives over false positives.
  - Any merge based only on name similarity should be `needs_review` unless reinforced by a strong signal.
- Additive schema evolution:
  - Existing Phase 6 tables remain; add new tables and bump `SCHEMA_VERSION`.

## Data Contracts (Additive)

### A) New Phase 5-style JSONL: `identity_signals.jsonl`

Add a new extraction module (similar shape to Phase 5) to produce per-chunk identity signals.
Proposed JSONL record shape:

```json
{
  "file_id": "…",
  "chunk_id": "…",
  "page_range": [1, 2],
  "items": [
    {
      "person": "Robert Smith",
      "attribute": "dob|address|case_id",
      "value": "…",
      "quote": "…",
      "confidence": 0.0
    }
  ],
  "model": "…",
  "error": null
}
```

Notes:
- Keep `items` strict and small. Use an empty list when none are found.
- `quote` is a short verbatim span from the chunk text.
- `person` is the surface string; resolution will link it to a person observation.

### B) New SQLite tables (schema v2+)

Add tables under `src/loaders/store.py` with the same patterns as Phase 6 (unique keys, indexes, idempotent loads).

1) `identity_signals`
- `signal_id INTEGER PRIMARY KEY`
- `person_text TEXT NOT NULL` (surface person string from extraction)
- `attribute TEXT NOT NULL` (enum-ish: `dob`, `address`, `case_id`)
- `value TEXT NOT NULL` (raw)
- `value_norm TEXT` (normalized for joins: e.g. date -> YYYY-MM-DD, address -> canonical form)
- `confidence REAL`
- `file_id TEXT NOT NULL`
- `chunk_id TEXT NOT NULL`
- `page_start INTEGER`
- `page_end INTEGER`
- `quote TEXT`
- `UNIQUE(person_text, attribute, value_norm, file_id, chunk_id, page_start, page_end, quote)`

2) `person_observations`
- `obs_id INTEGER PRIMARY KEY`
- `name TEXT NOT NULL` (surface name)
- `name_norm TEXT NOT NULL` (normalized)
- `file_id TEXT NOT NULL`
- `chunk_id TEXT NOT NULL`
- `page_start INTEGER`
- `page_end INTEGER`
- `UNIQUE(name_norm, file_id, chunk_id, page_start, page_end)`

Source of truth for observations:
- Derived from `entities` rows where `type` looks like a person.
- If `type` is missing/unstable, fall back to a deterministic allowlist of type strings (configurable),
  and otherwise skip.

3) `person_clusters`
- `person_id INTEGER PRIMARY KEY`
- `display_name TEXT NOT NULL` (chosen canonical display name)
- `display_name_norm TEXT NOT NULL`
- `dob TEXT` (normalized, optional)
- `created_utc TEXT NOT NULL` (for auditing; not for clustering)

4) `person_cluster_members`
- `person_id INTEGER NOT NULL`
- `obs_id INTEGER NOT NULL`
- `PRIMARY KEY(person_id, obs_id)`

5) `person_resolution_edges` (audit + explainability)
- `left_obs_id INTEGER NOT NULL`
- `right_obs_id INTEGER NOT NULL`
- `decision TEXT NOT NULL` (`auto_merge|needs_review|no_merge`)
- `score REAL NOT NULL`
- `reasons_json TEXT NOT NULL` (small JSON list of reason codes, no raw PII)
- `PRIMARY KEY(left_obs_id, right_obs_id)`

Indexes:
- `person_observations(name_norm)`
- `identity_signals(attribute, value_norm)`
- `person_cluster_members(obs_id)`

## Resolution Algorithm (Deterministic Two-Phase)

### Phase 1: Candidate Generation (High Recall)

Build candidate pairs of `person_observations` using cheap, index-friendly joins:

- Exact match on normalized DOB (strong candidate).
- Exact match on normalized case ID (medium candidate; may be case-wide).
- Address match (normalized) AND name token overlap (medium/strong depending on quality).
- Name-based candidates:
  - same last name + first initial
  - nickname expansion (static map: `bob <-> robert`, etc.)
  - edit-distance or token similarity above threshold

Hard limits:
- Cap candidate fanout per observation (e.g. top N) to avoid O(n^2) blowups on common names.

### Phase 2: Scoring + Decisions (Precision)

For each candidate pair, compute a score from explainable components:

- `dob_exact`: +W (very strong)
- `address_exact_norm`: +W (strong)
- `case_id_exact`: +W (weak-to-medium; tune carefully)
- `name_similarity`: +W (token/Jaro-Winkler-like, deterministic)
- `nickname_match`: +W
- `negative_signals`: -W (e.g. conflicting DOBs when both present and different)

Decision thresholds:
- `auto_merge` requires at least one strong signal (DOB exact, or address exact + high name similarity).
- `needs_review` for name-only matches or case-id-only matches.
- `no_merge` when score is below threshold or conflicts are detected.

Clustering:
- Use union-find over `auto_merge` edges to build `person_clusters`.
- Do not allow `needs_review` edges to create clusters automatically; store them for review tooling later.

Canonical display name selection:
- Deterministic: choose the most frequent `name_norm`, break ties by highest-confidence evidence,
  then lexicographic order.

## Pipeline Integration

Add a new stage after Phase 6 load:

- New command: `fileparse resolve` (or similar), operating on `output/store.sqlite`.
- Optional addition to `fileparse run --steps …` as `resolve` after `load` and before `validate` (or after).

Outputs:
- New tables in the same SQLite DB (preferred for joinability).
- Optional JSONL export of cluster summaries for downstream systems (counts only, redacted by default).

## Observability (Without Data Leakage)

Resolver should print a deterministic summary:

- observations_total
- signals_total (by attribute)
- candidates_total
- edges_by_decision (auto_merge / needs_review / no_merge)
- clusters_total
- cluster_size_histogram (e.g. 1,2,3-5,6-10,>10)
- elapsed_seconds per stage (build observations, load signals, generate candidates, score+cluster)

Error handling:
- Collect and report counts by error type (invalid record shape, missing norms, decode errors).
- Include at most K redacted samples (hash of `name_norm` and `value_norm`, not raw strings).

## Codex-Ready TODO Checklist (When Implementing)

### 0) Baseline + Safety
[x] Run `git status --short` and confirm no unrelated edits.
[x] Run baseline tests (focused): `pytest -q tests/test_phase6_loaders.py`.

### 1) Add Identity Signal Extractor (Phase 5-style)
[x] Add `src/llm/extract_identity_signals.py` with strict JSON schema.
[x] Add CLI wiring under `fileparse llm …` (new subcommand).
[x] Keep temperature 0 and fail-soft error recording like other LLM extractors.

### 2) Add Loader + Schema v2
[x] Bump `SCHEMA_VERSION` and extend `ensure_schema` to create new tables/indexes.
[x] Add `src/loaders/load_identity_signals.py` mirroring Phase 6 loader patterns.
[x] Add `fileparse load identity-signals` (or include in `load all` via a flag).

### 3) Build Resolver
[x] Add `src/entity_resolution/phase8_resolve_people.py` (or similar).
[x] Implement: build observations, normalize, candidate generation, scoring, union-find clustering.
[x] Store results in `person_clusters`, `person_cluster_members`, `person_resolution_edges`.

### 4) CLI Integration
[x] Add `fileparse resolve` command and help text.
[x] Consider `fileparse run --steps …` support for `resolve` (off by default until stable).

### 5) Tests (Deterministic, No LLM Required)
[ ] Add `tests/test_phase8_resolve_people.py` using a temp SQLite DB:
  - alias case: "Robert Smith" + "Bob Smith" with shared DOB => auto_merge
  - shared case ID only => needs_review
  - same name, conflicting DOB => no_merge
  - address+name match across files => auto_merge (if strong enough)
[ ] Add loader tests for `identity_signals` similar to `tests/test_phase6_loaders.py`.

### 6) Documentation
[ ] Update `README.md` with resolver usage and tables created.
[ ] Add operator note about conservative thresholds and review workflow.

## Acceptance Criteria

- Resolver produces stable results for the same input DB/config (deterministic).
- Duplicate people across files are clustered when strong signals match (DOB/address + name).
- Aliases are handled at least via nickname map + name normalization.
- Case-ID-only links do not auto-merge; they surface as `needs_review`.
- One bad row or missing signals does not crash the run; errors are summarized.
- Tests for resolver and signal loader pass in a clean environment.

## Commands To Run During Implementation (When Approved)

- Search for type/mention assumptions:
  - `rg -n "mentions\\(|entities\\(|type\\b|schema_version" -S src tests README.md`
- Focused tests:
  - `pytest -q tests/test_phase6_loaders.py`
  - `pytest -q tests/test_phase8_resolve_people.py`
- Full suite:
  - `pytest -q`
