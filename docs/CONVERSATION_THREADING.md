# L5 Plan: Conversation Threading

This document is an implementation plan for `docs/TODO_LONG_TERM.md` item **L5 — Conversation Threading**:

- Group related dialogues across multiple documents.
- Detect recurring participants.
- Label topics automatically.

This is a plan only. Do not implement until explicitly instructed.

## Goal

Given the current pipeline outputs (Phase 5 conversations JSONL + Phase 6 SQLite tables, optionally Phase 8 people resolution and Phase 9 timeline anchors), add a deterministic, fail-soft “conversation threading” stage that:

1) groups conversation **segments** into cross-document **threads**, and
2) attaches stable participant identifiers (when available) and topic labels.

## Non-Goals

- No UI (threads can be exported, but no viewer).
- No graph database (keep SQLite).
- No printing or logging raw quotes by default.
- No probabilistic “best guess” merging that can’t be explained (store reason codes for links).
- No breaking changes to existing Phase 5/6 outputs (additive only).

## Current Baseline (What Exists Today)

- Phase 5 conversation extraction (`src/llm/extract_conversations.py`) outputs per chunk:
  - `items[]` with `{ speaker, quote, confidence }`
- Phase 6 loader stores them to SQLite (`src/loaders/load_conversations.py`, `src/loaders/store.py`):
  - `conversations(conversation_id, speaker, confidence, file_id, chunk_id, page_start, page_end, quote)`
- Optional downstream signals already exist:
  - Phase 8 people resolution tables: `person_clusters`, `person_cluster_members`, `person_observations`
  - Phase 9 timeline tables: `event_times`, `event_cases` (case links derived from `identity_signals`)

Implications:
- `conversations.speaker` and `conversations.quote` must be treated as **untrusted raw text**.
- Threading should work even when Phase 8/9 have not been run, using deterministic fallbacks.

## Definitions

- **Utterance**: one row in `conversations` (a speaker + short verbatim quote).
- **Segment**: the unit we thread across documents; default = all utterances in the same `(file_id, chunk_id)`.
- **Participant**: a speaker identity attached to a segment/thread:
  - preferred: `person_id` (from Phase 8 resolution),
  - fallback: normalized `speaker_norm`.
- **Thread**: a cluster of segments believed to be part of the same conversation context across files.
- **Topic label**: a short human-readable label for a thread, produced deterministically (and optionally refined).

## Design Decisions (Lock Before Coding)

- Determinism:
  - Same inputs + same config => same thread assignments and labels.
  - Use rule-based tokenization and scoring; any optional LLM labeling must be off by default.
- Fail-soft:
  - Bad rows or malformed text do not crash the run; they’re skipped and counted.
- Privacy / data leakage:
  - Console logs must not print raw quotes, raw speaker strings, or file paths.
  - Debug output uses hashes/truncated samples only (similar to Phase 9 redaction).
- Additive schema evolution:
  - Keep `conversations` intact; add new tables for segment features + threads.
- Performance:
  - Avoid O(n²) comparisons by using inverted indexes and hard fanout caps.

## Data Contracts (Additive)

### A) New SQLite tables (schema v5+)

1) `conversation_segments` (one per `(file_id, chunk_id)` with derived fields)

- `segment_id INTEGER PRIMARY KEY AUTOINCREMENT`
- `file_id TEXT NOT NULL`
- `chunk_id TEXT NOT NULL`
- `page_start INTEGER`
- `page_end INTEGER`
- `case_id_norm TEXT NOT NULL` (fallback to `file_id`-scoped stable key)
- `case_source TEXT NOT NULL` (`identity_signals|event_cases|fallback_file`)
- `anchor_date TEXT` (ISO `YYYY-MM-DD`, nullable; derived deterministically if available)
- `utterance_count INTEGER NOT NULL`
- `participants_json TEXT NOT NULL` (small JSON list; no raw quotes)
- `features_json TEXT NOT NULL` (small JSON: token/entity signatures; no raw quotes)
- `UNIQUE(file_id, chunk_id)`

2) `conversation_thread_edges` (audit trail for why two segments were linked)

- `left_segment_id INTEGER NOT NULL`
- `right_segment_id INTEGER NOT NULL`
- `score REAL NOT NULL`
- `decision TEXT NOT NULL` (`link|no_link|needs_review`)
- `reasons_json TEXT NOT NULL` (small JSON list of reason codes; no raw text)
- `PRIMARY KEY(left_segment_id, right_segment_id)`

3) `conversation_threads` (thread clusters)

- `thread_id INTEGER PRIMARY KEY AUTOINCREMENT`
- `case_id_norm TEXT NOT NULL`
- `thread_key TEXT NOT NULL` (stable fingerprint of the cluster; used for idempotency)
- `label TEXT` (nullable; deterministic label)
- `label_method TEXT NOT NULL` (`keywords_v1|none|llm_v1`)
- `created_utc TEXT NOT NULL` (for auditing only)
- `UNIQUE(case_id_norm, thread_key)`

4) `conversation_thread_segments` (thread membership)

- `thread_id INTEGER NOT NULL`
- `segment_id INTEGER NOT NULL`
- `sort_key TEXT NOT NULL` (deterministic: `anchor_date` then `(file_id, chunk_id)` fallback)
- `PRIMARY KEY(thread_id, segment_id)`

5) `conversation_thread_participants` (thread participants)

- `thread_id INTEGER NOT NULL`
- `person_id INTEGER` (nullable)
- `speaker_norm TEXT` (nullable; present when `person_id` is null)
- `source TEXT NOT NULL` (`person_clusters|speaker_norm`)
- `PRIMARY KEY(thread_id, source, person_id, speaker_norm)`

Indexes (proposed):
- `conversation_segments(case_id_norm)`
- `conversation_threads(case_id_norm)`
- `conversation_thread_segments(segment_id)`
- `conversation_thread_participants(person_id)`

Notes:
- Keep derived JSON small and strictly non-sensitive; never store raw quotes outside `conversations`.
- `thread_key` should be a deterministic hash of the sorted `segment_id`s or sorted stable segment fingerprints.

### B) New command (Phase 10-style): conversation threading

Add a new module (proposed): `src/conversation_threading/phase10_thread_conversations.py` with:

- Inputs: `--db output/store.sqlite`
- Optional inputs:
  - `--chunks output/text/chunks.jsonl` (to derive chunk-level anchors or richer token features)
  - `--manifest output/manifest.jsonl` or rely on `files` table (file-level anchors)
- Output: new tables in the same SQLite DB (preferred for joinability)
- Optional exports:
  - `--export-jsonl output/conversation_threads.jsonl` (IDs + labels + participant IDs; no quotes by default)

CLI integration (proposed):
- Add `fileparse thread` and allow `fileparse run --steps …,thread,…` after `resolve` and `timeline`.

## Threading Algorithm (Deterministic Two-Phase)

### Phase A: Build segment records + lightweight features

For each `(file_id, chunk_id)` with ≥1 utterance:

1) **Participants**
   - `speaker_norm` = normalize `conversations.speaker` using the same canonicalization rules as Phase 8
     (lowercase, non-alnum collapse, whitespace normalize).
   - If Phase 8 tables exist:
     - map `speaker_norm` → `person_id` by exact match against `person_clusters.display_name_norm`
       (and optionally a conservative alias rule set; deterministic).
   - Store participants as a list of `{ person_id? , speaker_norm, source }`.

2) **Case grouping**
   - Preferred case key: `identity_signals(attribute='case_id')` joined on `(file_id, chunk_id)`.
   - Fallback: if Phase 9 `event_cases` exists, reuse its `case_id_norm` via joins on `(file_id, chunk_id)`.
   - Final fallback: `case_id_norm = "file:" + file_id` (prevents cross-file blending).

3) **Anchor date**
   - If Phase 9 anchor logic is available (chunks/manifest/files table), reuse the same priority order:
     chunk anchor → file mtime anchor → null.

4) **Content features (no raw quotes stored)**
   - Tokenize quotes deterministically:
     - lowercase, strip punctuation, split on whitespace
     - remove a fixed stopword list (checked into repo)
     - cap per-segment token counts to avoid outliers
   - Enrich with extracted entities/events in the same chunk (optional but deterministic):
     - include non-person entities as “topic tokens” (e.g., org/location/product)
   - Produce:
     - `topic_tokens_topk` (sorted with deterministic tie-breakers)
     - `topic_signature` = hash of the token multiset (or top-k set)

Write one `conversation_segments` row per segment and collect per-segment feature sets in memory for Phase B.

### Phase B: Candidate generation → scoring → clustering

1) **Candidate generation (high recall, bounded)**
   - Build inverted indexes inside each `case_id_norm`:
     - participant keys: `person_id` (preferred) or `speaker_norm`
     - topic keys: top tokens / entity tokens
   - For each segment, generate candidates by shared keys with hard limits:
     - cap per-key fanout (e.g., ignore keys that map to >N segments)
     - cap total candidates per segment (e.g., top M by cheap overlap count)

2) **Scoring (explainable)**
   - Compute a deterministic score from:
     - participant overlap (Jaccard on participant IDs/keys)
     - topic overlap (Jaccard on top-k tokens)
     - optional time proximity bonus if both segments have `anchor_date`
   - Convert to a decision:
     - `link` if score ≥ threshold and shared participant(s) exist
     - `needs_review` if topical match is strong but participants are weak/unknown
     - `no_link` otherwise
   - Store one `conversation_thread_edges` row per evaluated pair with reason codes only.

3) **Clustering**
   - Use union-find over `link` edges to form thread clusters within each `case_id_norm`.
   - Do not auto-merge on `needs_review` edges; store them for future review tooling.

4) **Thread materialization**
   - For each cluster:
     - compute `thread_key` deterministically (e.g., hash of sorted `segment_id`s)
     - insert into `conversation_threads`
     - insert members into `conversation_thread_segments` with deterministic `sort_key`
     - compute participants union and insert into `conversation_thread_participants`

## Topic Labeling (Deterministic Default + Optional Refinement)

Baseline (default):
- `label_method=keywords_v1`
- Label = deterministic string built from:
  - top non-person entity token(s) if present, else
  - top topic token(s), else
  - “Conversation: {top_participant} …”

Optional (off by default):
- `label_method=llm_v1`
- Use LLM only to turn *already-derived* top tokens/entities into a nicer short label.
- Never provide raw quotes to the labeler by default; require an explicit `--include-quotes-for-labeling` flag.

## Observability (Without Data Leakage)

Stage summary (stdout) includes counts only:
- segments built, utterances read, segments threaded, threads created
- candidate pairs generated, edges evaluated, links made, needs_review count
- mapping rates: speakers→person_id resolved vs fallback
- error counts by category (invalid shapes, missing fields, JSON errors if any)

Redacted samples (optional debug):
- hashed `topic_signature` and hashed `speaker_norm` only, capped to a small N.

## Implementation Checklist

- [x] Lock thread unit: confirm “segment = (file_id, chunk_id)” is the right default, or define a deterministic splitter for long chunks.
- [x] Specify and freeze normalization rules for `speaker_norm` (reuse Phase 8 canonicalization where possible).
- [x] Define and check in a fixed stopword list + tokenization rules (deterministic, versioned).
- [x] Finalize additive SQLite schema (tables + indexes) and bump `SCHEMA_VERSION`.
- [x] Implement Phase 10 module `src/conversation_threading/phase10_thread_conversations.py` with `build_*` helper and summary output.
- [x] Implement segment builder (participants, case grouping, anchor date derivation, feature hashing).
- [x] Implement bounded candidate generation with hard fanout caps (avoid O(n²)).
- [x] Implement scoring + decision thresholds with explainable reason codes.
- [x] Implement clustering (union-find) and thread materialization tables.
- [x] Implement deterministic topic labeling (`keywords_v1`) and store `label_method`.
- [x] (Optional) Implement LLM label refinement behind explicit flags; default must remain deterministic/offline.
- [x] Add CLI integration (`fileparse thread`) and wire into `fileparse run --steps …`.
- [x] Add Phase 7 validation checks for the new tables (row counts, foreign key shape, idempotency on reruns).
- [x] Add tests:
  - [x] unit tests for normalization/tokenization determinism
  - [x] unit tests for candidate generation caps
  - [x] unit tests for clustering determinism on a tiny synthetic DB
  - [x] regression test: rerun threading twice => identical thread assignments
