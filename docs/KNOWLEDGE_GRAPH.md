# L6 — Knowledge Graph (Implementation Plan)

This document is an implementation plan for item **L6 — Knowledge Graph** in `docs/TODO_LONG_TERM.md`.
It is **not** an instruction to start implementing immediately.

## Goal (What “done” means for L6)

Move relationship exploration from ad-hoc SQLite joins to a **graph-shaped store** that can answer traversal
queries across **people**, **events**, and **cases** while preserving Parsnip’s core invariants:

- **Deterministic + idempotent** builds (reruns converge on the same graph).
- **Auditable evidence pointers** on every relationship (file/chunk/page/quote + extractor version).
- **Fail-soft** behavior (bad records don’t crash a build; errors are summarized).

Storage staging must follow `docs/GRAPH_DB_DECISION.md`:

- Stage 1: SQLite remains the system-of-record, but models the graph explicitly (stable IDs + edge tables).
- Stage 3: migrate edges/traversals into a graph DB only after triggers are met (query patterns + scale).

## Non-goals

- Building a UI (that is L7).
- Rewriting entity resolution / timeline / conversation threading logic (L3–L5 remain authoritative).
- Making a graph DB the system-of-record before IDs/contracts stabilize.

## “Must-answer” queries (requirements-first)

Before choosing a graph engine or schema details, write down 5–10 concrete queries (with expected scale):

- [x] **Case overview**
  - Input: `case_id_norm`, optional `date_start/date_end`, optional `limit_*`.
  - Output: top people (by edge count + recency), ordered events, top conversation threads, plus evidence links.
  - Evidence requirement: every surfaced relationship must be traceable to `(file_id, chunk_id, page_*, source_phase)`.
- [x] **Person profile**
  - Input: `person_id`, optional `case_id_norm`, optional `date_start/date_end`.
  - Output: aliases/observations, linked cases, linked events, top conversation threads, plus evidence links.
  - Evidence requirement: “why linked” must be explainable (edge evidence + confidence).
- [x] **Person ↔ person traversal**
  - Input: `person_id`, `hops in {1,2}`, optional constraints: `case_id_norm`, date range, edge types allowed.
  - Output: reachable people with path summaries (edge types + counts), plus evidence links for each hop.
  - Safety requirement: caps/limits on fanout to avoid “edge explosion” queries.
- [x] **Event storyline**
  - Input: `case_id_norm`, optional date range.
  - Output: events ordered by normalized `event_times.date_start`, each with participants + evidence.
  - Correctness requirement: timeline ordering uses Phase 9 normalization outputs (status-aware).
- [x] **Explainability**
  - Input: edge ID or `(src, edge_type, dst)` plus optional evidence filters.
  - Output: the evidence rows that created/justified the edge (no raw quotes by default).

Scale assumptions for v0 (to validate in real data):

- Total documents up to ~3.5M (per `docs/GRAPH_DB_DECISION.md`).
- Graph scope for most interactive work is per-case (case neighborhoods), not global all-nodes traversals.

Acceptance criteria should include:

- [x] Target latency class (batch-only vs interactive).
  - Stage 1 (SQLite edges): batch build + offline analysis queries are acceptable.
  - Stage 3 (graph DB mirror): interactive traversal (1–2 hops) becomes a goal *only if triggers are met*.
- [ ] Edge volumes (edges per person, per case, total edges).
  - Measure from SQLite once Stage 1 edge tables exist; set caps and indexing based on observed distributions.
  - Intentionally left unchecked until Stage 1 edge tables exist and we can measure real distributions.
- [x] Redaction policy for logs/summaries (no raw quotes by default).
  - Store evidence pointers; log only counts + hashed/redacted samples. Quotes remain opt-in.

## Data model (canonical property graph)

Define a small, stable set of node types and edge types. Prefer **fewer primitives** with strong evidence.

### Nodes (stable IDs)

- `Person(person_id)` from `person_clusters.person_id` (Phase 8)
- `Event(event_id)` from `events.event_id` (loaded + Phase 9 normalized)
- `Case(case_id_norm)` derived from normalized case IDs (Phase 8/9/10 signals)
- Optional (later, if it pays off):
  - `ConversationThread(thread_id)` from `conversation_threads.thread_id` (Phase 10)
  - `Document(file_id)` from `files.file_id` (Phase 6 load_manifest)

Node properties (KG schema v1):

- `Person`
  - Required: `person_id`, `display_name`, `display_name_norm`
  - Optional: `dob`
- `Event`
  - Required: `event_id`, `event` (text)
  - Optional: `date_raw` (source string), normalized fields from Phase 9 (`date_start/date_end/precision/status`)
- `Case`
  - Required: `case_id_norm`
  - Optional: `case_id_display` (one representative raw form), `sources` (provenance summary)
- `ConversationThread` (optional)
  - Required: `thread_id`, `case_id_norm`, `thread_key`
  - Optional: `label`, `label_method`
- `Document` (optional)
  - Required: `file_id`
  - Optional: `source_type`, `mtime_utc`, `size_bytes`
  - Do not export raw filesystem paths by default (`container_path`, `virtual_path`) unless explicitly needed.

### Edges (all edges carry evidence)

Minimum set to satisfy L6:

- `Event -[:IN_CASE]-> Case` (from `event_cases`)
- `Person -[:MENTIONED_IN_EVENT]-> Event` (via co-occurrence in the same `file_id/chunk_id` + evidence)
- `Person -[:IN_CASE]-> Case` (derived from identity signals / event participation with evidence)

Optional enrichment edges (only if they improve query quality):

- `Person -[:PARTICIPATED_IN_THREAD]-> ConversationThread` (from `conversation_thread_participants`)
- `Event -[:MENTIONED_IN_THREAD]-> ConversationThread` (from shared `file_id/chunk_id` signals)
- `Person -[:CO_OCCURS_WITH]-> Person` (materialized view, not canonical, if traversal is too slow)

Evidence pointer fields (required on every edge):

- `file_id`, `chunk_id`, `page_start`, `page_end`, `quote` (quote optional/off by default)
- `confidence`, `source_phase`, `extractor_version`, `created_utc`

Edge identity (deterministic):

- Canonical logical edge key: `(src_type, src_id, edge_type, dst_type, dst_id)`.
- Evidence rows are append-only and uniquely keyed so reruns are idempotent (no duplicate evidence inserts).

## Implementation plan (staged)

### Phase A — Specify graph outputs and invariants (paper design)

Deliverables:

- [x] A one-page list of **must-answer queries** with example inputs/outputs and scale assumptions.
- [x] A canonical **node/edge schema** (types + required properties).
  - Node/edge contract version: `kg_schema_version = 1` (bump only when breaking).
- [x] Determinism rules:
  - [x] stable IDs only (no UUIDs unless derived deterministically)
  - [x] sorted/partitioned exports for repeatable builds
  - [x] idempotent edge materialization (unique keys + `INSERT OR IGNORE` semantics)
  - [x] evidence-first (edges may be many-to-one; do not collapse evidence lossily)

### Phase B — Stage 1 storage: “SQL-first graph shape” (SQLite)

Goal: represent the knowledge graph *explicitly* in SQLite so it’s auditable, joinable, and easy to export.

Work items:

- [x] Add/standardize a `cases` table keyed by `case_id_norm` (display form + provenance).
- [x] Add explicit edge tables (or one generic edge table) with:
  - [x] `(src_type, src_id, edge_type, dst_type, dst_id)` + evidence pointers + confidence/versioning
  - [x] unique constraints to ensure idempotence (`INSERT OR IGNORE` style)
- [x] Backfill edges deterministically from existing tables:
  - [x] `event_cases` ⇒ `Event IN_CASE Case`
  - [x] `person_cluster_members` + `person_observations` joined on `(file_id, chunk_id)` with `events`
    ⇒ `Person MENTIONED_IN_EVENT Event`
  - [x] `identity_signals(attribute='case_id')` attached to a person observation/cluster
    ⇒ `Person IN_CASE Case`
- [x] Add indexes aligned to the must-answer queries (case_id_norm, person_id, event_id, edge_type).

Phase B entry point:

- `PYTHONPATH=src python -m knowledge_graph.phase11_materialize_edges --db output/store.sqlite --reset`

Notes:

- Prefer recording multiple evidence rows per logical edge (many-to-one) rather than collapsing evidence.
- Keep raw text/quotes out of logs by default; store quotes only when necessary for explainability.

### Phase C — Build deterministic exports (“graph projection”)

Goal: make the graph portable without committing to a specific graph DB too early.

Work items:

- [ ] Define an export format:
  - [ ] `output/kg/nodes/*.jsonl.zst` and `output/kg/edges/*.jsonl.zst`
  - [ ] stable ordering (by type, then ID) so diffs are meaningful
- [ ] Add a single CLI entry point (proposed):
  - [ ] `PYTHONPATH=src python -m knowledge_graph.phase11_build_kg --db output/store.sqlite --out output/kg`
- [ ] Add lightweight validation on export:
  - [ ] referential integrity (every edge endpoint exists)
  - [ ] required properties present
  - [ ] counts + top-k edge types summary (no raw values)

### Phase D — Stage 3: Graph DB mirror + traversal queries (when triggers are met)

Only start this phase when `docs/GRAPH_DB_DECISION.md` triggers apply (traversal dominates, SQL painful).

Work items:

- [ ] Pick one graph engine for the *first* integration (opt for operational simplicity):
  - [ ] Neo4j (property graph) is the most direct fit for node/edge modeling.
  - [ ] Keep SQLite as system-of-record; the graph DB is a read-optimized mirror at first.
- [ ] Write an ingestion tool that loads the Phase C exports into the graph DB deterministically.
- [ ] Implement a minimal query layer that answers the must-answer queries, with edge-level evidence links
  back to SQLite rows (or exported evidence IDs).
- [ ] Add parity checks:
  - [ ] for a sample of cases/people, compare query results between SQLite joins and graph traversal.

### Phase E — Cutover strategy (optional, later)

If/when the graph DB becomes the preferred traversal backend:

- [ ] Keep write-path in SQLite; publish-to-graph as a build step (eventually streaming if justified).
- [ ] Treat the graph DB as a cache: rebuildable from SQLite + extraction outputs.
- [ ] Define operational requirements:
  - [ ] backups, migrations, auth, resource sizing, and failure modes.

## Risks and mitigations

- [ ] **Schema churn** (L3–L5 still evolving): keep Stage 1 as additive edge tables; export format versioned.
- [ ] **Edge explosion** (too many co-occurrence edges): require case scoping + confidence thresholds + caps.
- [ ] **Non-determinism** (parallel builds): enforce stable ordering + uniqueness keys; record build metadata.
- [ ] **Data leakage** (quotes/paths): store evidence pointers, but redact/truncate in logs and summaries.

## Open questions (to resolve before Phase D)

- [ ] Which 2–3 traversal queries are truly “core” and need interactive performance?
- [ ] Expected edge volumes by type at the target scale (e.g., edges/person/case).
- [ ] Evidence retention policy: do we store quotes for every edge, or only evidence IDs + “show on demand”?
