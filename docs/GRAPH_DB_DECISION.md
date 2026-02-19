# Storage Staging Decision: Graph DB vs SQL (3.5M Files)

This document records a concrete, staged storage decision for scaling Parsnip toward:

- character profiles from email interactions
- identifying characters in images
- storylines from conversations + dates + characters

This is guidance for later work items (L3-L6). It is not an implementation request.

## Problem Statement

As we ingest and extract signals from ~3.5 million files, we need to:

- assign stable IDs to real-world entities (people/characters)
- preserve evidence links (file/chunk/page/quote/image region)
- support storyline queries that traverse relationships across many documents
- keep the system operationally simple while extraction contracts are still evolving

## Current Baseline

The active pipeline already has a SQLite-backed store with deterministic loaders (Phase 6),
and JSONL extraction outputs (Phase 5). This favors:

- batch ingestion
- idempotent reruns
- explainable/auditable records

## Decision (Staged)

### Stage 1 (Now): SQLite as system-of-record + explicit edge tables

Use SQLite as the authoritative store for:

- canonical IDs (e.g. `person_id`)
- extracted attributes (DOB/address/case IDs) with evidence pointers
- relationships stored explicitly as edges (`person -> conversation`, `person -> event`, etc.)
- entity-resolution audit trails ("why merged", "why not merged")

This keeps L3 entity resolution join-friendly and deterministic, while representing a graph shape
in a relational database.

### Stage 2 (Next): Add vector indexes for similarity search (text + faces)

Use a vector index optimized for nearest-neighbor search, separate from the system-of-record:

- text embeddings for emails/chunks (for semantic recall)
- face embeddings for image regions (for cross-image identity)

Store vector IDs and their provenance in SQLite so all results remain auditable and joinable.

### Stage 3 (Later): Migrate edges to a graph database when traversal queries dominate

Move relationship exploration to a graph DB (Neo4j, Neptune, etc.) only once:

- entity IDs and extraction contracts are stable (post-L3/L4/L5)
- we have confirmed query patterns that are painful/slow in SQL
- we can justify operational complexity and migration effort

The key requirement is that Stage 1 models relationships as explicit edges with stable node IDs,
so Stage 3 is primarily a storage migration, not a logic rewrite.

## Why Not Graph First

- Entity resolution is algorithmic and evidence-driven; it does not become easier just because the
  storage layer is a graph.
- Early-stage schemas change. SQLite is low-friction for iterative, additive evolution and batch loads.
- Graph DBs add operational surface area (hosting, auth, backups, migrations, drivers) before we know
  which traversals matter most.

## "Must Answer" Queries (Define the Target)

These queries determine whether SQLite + edge tables remains sufficient, or a graph DB is justified.
Capture these as concrete examples with expected latency and scale:

1) Character profile
- Given `person_id`, show: names/aliases, key attributes (DOB/address/case IDs), top conversations,
  top linked events, timeline summary, evidence pointers.

2) Alias exploration
- Given "Bob Smith", find candidate `person_id` values with reasons and evidence.

3) Storyline construction
- For a case, compute an ordered storyline: events + conversations + participants + dates,
  with evidence and confidence.

4) Relationship traversal
- "Find all people within 2 hops of person X via conversations/events" with constraints
  (date range, case IDs, location).

5) Cross-modal identity
- Given a face embedding match, link image regions to `person_id` and surface corroborating text evidence.

## Stage 1 Data Model (SQL-First Graph Shape)

Keep the system-of-record in SQLite with explicit nodes and edges. Minimal pattern:

- Nodes:
  - `people` (canonical IDs)
  - `conversations`
  - `events`
  - `documents/files` (already tracked via `file_id` in outputs)
- Edges (examples):
  - `person_participated_in_conversation(person_id, conversation_id, file_id, chunk_id, page_start, page_end, quote, confidence)`
  - `person_mentioned_in_event(person_id, event_id, ...)`
  - `conversation_mentions_event(conversation_id, event_id, ...)`

Evidence pointer columns are not optional. They are required to keep merges and storylines explainable.

Performance knobs:
- Use normalized keys + indexes for candidate generation (DOB/case ID/address/name_norm).
- Use batch inserts and `INSERT OR IGNORE` with unique keys for idempotence.

## Stage 2 Vector Index Integration

Do not treat the vector index as the system-of-record. Instead:

- Store embeddings in the vector engine.
- Store metadata pointers in SQLite:
  - vector_id -> (file_id, chunk_id/page, image region, extractor version, model id, created_utc)
- Record similarity matches as edges or as query-time results with evidence pointers.

This avoids tight coupling between retrieval infrastructure and canonical identity logic.

## When To Migrate to a Graph DB (Explicit Triggers)

Move edges/traversals to a graph DB when at least one of these is true:

- Multi-hop traversal queries (2-5 hops) become core product workflows and are too slow/complex in SQL.
- The number of edges is large enough that SQL join performance or query ergonomics blocks iteration.
- You need interactive exploration across large neighborhoods with stable latency targets.

Do not migrate just because the data is "graph-shaped".

## Migration Strategy (Make It Cheap Later)

To keep a future migration low-risk:

- Ensure all nodes have stable integer/string IDs (`person_id`, `event_id`, etc.).
- Ensure all edges have explicit tables with:
  - node IDs
  - edge type
  - evidence pointers
  - confidence / model versioning
- Avoid embedding logic into the storage layer (no graph-only procedures required for correctness).

## Open Questions (Resolve Before Committing to Stage 3)

- What are the top 10 queries and their latency expectations (batch vs interactive)?
- What is the expected edge volume (edges per person, per case)?
- How often do we rerun extraction/resolution (daily, weekly), and how do we handle versioning?
- What is the review workflow for ambiguous merges (`needs_review`)?

