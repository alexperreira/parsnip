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

- [ ] **Case overview**
  - Given `case_id_norm`, list key people, events, and conversation threads with evidence.
- [ ] **Person profile**
  - Given `person_id`, list aliases, linked cases, linked events, top conversation threads, evidence.
- [ ] **Person ↔ person traversal**
  - “People within 2 hops of person X via shared conversation thread/event/case,” with constraints.
- [ ] **Event storyline**
  - For a case, return events ordered by `event_times.date_start`, including linked participants.
- [ ] **Explainability**
  - For any edge in results, show the evidence pointers that created it.

Acceptance criteria should include:

- [ ] Target latency class (batch-only vs interactive).
- [ ] Edge volumes (edges per person, per case, total edges).
- [ ] Redaction policy for logs/summaries (no raw quotes by default).

## Data model (canonical property graph)

Define a small, stable set of node types and edge types. Prefer **fewer primitives** with strong evidence.

### Nodes (stable IDs)

- `Person(person_id)` from `person_clusters.person_id` (Phase 8)
- `Event(event_id)` from `events.event_id` (loaded + Phase 9 normalized)
- `Case(case_id_norm)` derived from normalized case IDs (Phase 8/9/10 signals)
- Optional (later, if it pays off):
  - `ConversationThread(thread_id)` from `conversation_threads.thread_id` (Phase 10)
  - `Document(file_id)` from `files.file_id` (Phase 6 load_manifest)

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

## Implementation plan (staged)

### Phase A — Specify graph outputs and invariants (paper design)

Deliverables:

- [ ] A one-page list of **must-answer queries** with example inputs/outputs and scale assumptions.
- [ ] A canonical **node/edge schema** (types + required properties).
- [ ] Determinism rules:
  - [ ] stable IDs only (no UUIDs unless derived deterministically)
  - [ ] sorted/partitioned exports for repeatable builds

### Phase B — Stage 1 storage: “SQL-first graph shape” (SQLite)

Goal: represent the knowledge graph *explicitly* in SQLite so it’s auditable, joinable, and easy to export.

Work items:

- [ ] Add/standardize a `cases` table keyed by `case_id_norm` (display form + provenance).
- [ ] Add explicit edge tables (or one generic edge table) with:
  - [ ] `(src_type, src_id, edge_type, dst_type, dst_id)` + evidence pointers + confidence/versioning
  - [ ] unique constraints to ensure idempotence (`INSERT OR IGNORE` style)
- [ ] Backfill edges deterministically from existing tables:
  - [ ] `event_cases` ⇒ `Event IN_CASE Case`
  - [ ] `person_cluster_members` + `person_observations` joined on `(file_id, chunk_id)` with `events`
    ⇒ `Person MENTIONED_IN_EVENT Event`
  - [ ] `identity_signals(attribute='case_id')` attached to a person observation/cluster
    ⇒ `Person IN_CASE Case`
- [ ] Add indexes aligned to the must-answer queries (case_id_norm, person_id, event_id, edge_type).

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
