# Backend Contract (Shared Core for L9 Options A/B/C)

This doc is an implementation plan for a shared backend contract that can power:

- Option A (Investigator Tool)
- Option B (Search System)
- Option C (Narrative Engine)

The intent is to build one set of primitives (data model + storage + query APIs + provenance rules) and then
layer product surfaces on top.

## Goals

- One canonical "truth layer" for documents, chunks, entities, events, claims, and relationships.
- Deterministic IDs and stable "product paths" so links/bookmarks do not break as the pipeline evolves.
- Strong provenance: every derived fact (entity/event/claim/edge) points back to evidence spans.
- Retrieval-first: every synthesis artifact is generated from an explicit retrieved set and includes citations.
- Fail-soft ingestion: one bad input record does not crash a run; errors are collected and summarized.

## Non-Goals (For This Phase)

- Building a web service or multi-tenant SaaS. Start as a local library + CLI-backed API.
- Perfect entity resolution or timeline correctness. The contract must support improvements, not require them.
- Choosing a final graph database. Keep SQLite as the source of truth until scale forces a change.

## Contract Surfaces (What Must Be Stable)

### Stable IDs

- `file_id`: existing SHA-256 identity (`source_type|container_path|virtual_path`).
- `chunk_id`: deterministic `"{file_id}:{page_start}-{page_end}"` (already used today).
- `case_id_norm`: normalized case identifier (already used in conversation/timeline stages).
- `artifact_id`: deterministic ID for derived outputs (see "Artifacts").

[ ] Write down the canonical ID rules (including normalization and forbidden characters) and treat changes as breaking.

### Evidence Pointer (Provenance)

Every extracted/synthesized item that asserts something must include at least one `EvidenceRef`:

- `file_id`, `chunk_id`
- `page_start`, `page_end`
- Optional `char_start`, `char_end` relative to the chunk text
- Optional `quote` (short snippet) with redaction rules and length caps
- `source_phase` (e.g. `phase4_chunk`, `llm.extract_events`, `phase11_build_kg`)
- `extractor_version` (git SHA or semantic version)
- Optional `model` and `prompt_hash` (store hashes, not full prompts)
- `confidence` as a float in `[0, 1]` (when available)

[ ] Standardize an `EvidenceRef` shape and require it in loaders + downstream tables for anything "claim-like".

### Product Paths (Navigation Keys)

"Product paths" are human-readable, stable references for UI and deep links. They should be derived from IDs
and not depend on filenames that can change.

Example path families (exact syntax can evolve, but mapping must be deterministic):

- `case/{case_id_norm}`
- `case/{case_id_norm}/doc/{file_id}`
- `case/{case_id_norm}/chunk/{chunk_id}`
- `case/{case_id_norm}/person/{person_id}`
- `case/{case_id_norm}/event/{event_id}`
- `case/{case_id_norm}/thread/{thread_id}`
- `case/{case_id_norm}/artifact/{artifact_id}`

[ ] Define and document the path mapping rules (including how a "default case" is determined when unknown).

## Data Model (Core Primitives)

This is the minimal shared vocabulary needed by A/B/C:

- Document: source identity + file metadata + classification + parse status
- Page: per-page text/OCR + quality signals (stored in artifacts; DB stores summary + pointers)
- Chunk: page range + signals + pointers to text
- EntityObservation: raw mentions with evidence
- Person: resolved/clustered people with edges back to observations
- Event: extracted event with normalized time + evidence
- Claim: a structured assertion (subject, predicate, object) with evidence
- RelationshipEdge: normalized edge tuples (`src_type, src_id, edge_type, dst_type, dst_id`) with evidence
- ConversationSegment / Thread: dialogue units with participants and evidence
- Artifact: synthesized outputs (summaries, profiles, narratives) that reference a retrieval set + citations

[ ] Decide which primitives are "observations" (append-only) vs "canonical" (can be updated/replaced by newer runs).

## Storage Plan (SQLite + Artifact Store)

### Source of Truth Split

- Artifact store (filesystem): large payloads (page text, chunk text, OCR outputs, LLM JSONL outputs).
- SQLite: searchable metadata, indices, normalized relationships, and evidence references.

[ ] Write down the rule: "SQLite stores pointers + summaries; artifacts store raw text" (with exceptions explicitly listed).

### SQLite Schema Extensions (Incremental)

Existing tables cover many primitives (files, entities, events, conversations, timeline, threads, KG edges).
Add only what is required for retrieval + claims + artifacts:

- `chunks` (if not already in main DB): `chunk_id`, `file_id`, `page_start`, `page_end`, `signals_json`, `text_ref`
- `chunk_text_refs`: pointer to artifact store location + byte offsets (for shard files)
- `claims`: `claim_id`, `subject`, `predicate`, `object`, `confidence`, `created_utc`, `source_phase`, `extractor_version`
- `claim_evidence`: `claim_id` + `EvidenceRef` columns
- `artifacts`: `artifact_id`, `artifact_type`, `scope_type`, `scope_id`, `inputs_json`, `output_ref`, timestamps, versions
- `artifact_citations`: `artifact_id` + `EvidenceRef` columns (plus optional `cite_label`)
- `retrieval_sets`: `set_id`, `query_json`, `created_utc`, `owner`, `notes`
- `retrieval_set_items`: `set_id`, `item_type`, `item_id`, `rank`, `score`

[ ] Add a migration mechanism (even a small hand-rolled one) so schema changes are explicit and reversible.

## Retrieval and Query APIs (Library-First)

Implement a Python query layer that A/B/C can share. Keep it synchronous and deterministic.

Minimum API surface:

- `get_document(file_id) -> Document`
- `get_chunk(chunk_id) -> Chunk + (optional) text`
- `search(query) -> ranked results` supporting:
  - semantic search (optional feature flag)
  - keyword search (SQLite FTS)
  - filters: person, date range, case, topic, file_id
- `get_case_overview(case_id_norm)` returning pointers to core objects (people, events, threads, artifacts)
- `create_retrieval_set(query) -> set_id` and `list_retrieval_set_items(set_id)`

Result contract requirements:

- Every result includes `EvidenceRef` or a pointer to evidence-bearing records.
- Every result includes `product_path`.

[ ] Add a `src/query/` module (or similar) with typed request/response objects (dataclasses or pydantic).

## Indexing Strategy (Keyword + Vector + Structured)

### Keyword

- Use SQLite FTS5 for `chunk` text (or for a chunk text preview) to support deterministic keyword search.
- Store only small previews in DB if full text is kept in artifacts.

[ ] Add an FTS-backed index for chunk search with clear size caps and redaction rules.

### Vector (Optional, But Designed In)

- Build embeddings offline from chunk text and store vectors in a local index (e.g. FAISS) plus a SQLite mapping.
- Keep the vector index rebuildable from artifacts; do not make vectors the only copy of text.

[ ] Define a "vector index contract": embedding model name/version, dimension, and how rebuilds are triggered.

### Structured Filters

- People: `person_id` (clusters) and/or `name_norm` (observations)
- Date: use normalized timeline fields (`event_times.date_start/date_end`)
- Case: `case_id_norm` on segments/events and case membership tables

[ ] Ensure each filter is backed by an index and has a clearly documented "unknown/none" behavior.

## Synthesis Artifacts (For Options A and C)

Synthesis is a pipeline stage that consumes a retrieval set and produces an artifact with citations.

Artifact rules:

- Inputs must be explicit: `retrieval_set_id` or explicit item list.
- Outputs must be stored in artifacts (filesystem) with a DB pointer + metadata.
- Citations are mandatory: store as `artifact_citations` referencing `EvidenceRef`.
- Artifacts are versioned and never overwrite in place; new versions can supersede old ones.

[ ] Create an artifact registry (types, schemas, versioning rules, and citation requirements).

## Observability (No Data Leakage)

- Track counts: files scanned, chunks indexed, entities/events/claims loaded, artifacts produced.
- Track timings per stage and per loader.
- Store error samples with redaction (hash or truncate sensitive strings).

[ ] Add a standard metrics summary block that each CLI stage prints (and optionally writes as JSON).

## Implementation Milestones (Recommended Order)

1. Contract and schema first (no product code)
   - [ ] Write this contract into enforceable schemas (JSON Schema or pydantic) for core records.
   - [ ] Add golden samples for each record type in `tests/fixtures/` (small, redacted).

2. Make chunks first-class in the main DB
   - [ ] Add `chunks` tables/pointers to the primary SQLite store (not just the chunk index DB).
   - [ ] Add a loader to ingest chunk metadata and text refs from `chunks.jsonl(.zst)`.

3. Search MVP (Option B foundation)
   - [ ] Add FTS5 keyword search over chunk previews (or full chunk text if acceptable).
   - [ ] Add `search()` query API + CLI command that returns `product_path` + evidence.

4. Claims and evidence normalization
   - [ ] Add `claims` + `claim_evidence` tables and loader.
   - [ ] Add a minimal claim extractor (can start rule-based; LLM can be a later stage).

5. Retrieval sets and citations
   - [ ] Add `retrieval_sets` tables and APIs.
   - [ ] Ensure all syntheses can point to a set id and reproduce inputs.

6. Artifacts for Investigator Tool and Narrative Engine
   - [ ] Add `artifacts` + `artifact_citations` tables and filesystem output convention.
   - [ ] Implement one artifact end-to-end: `case_summary` from a retrieval set.

7. Optional semantic search
   - [ ] Add an embeddings builder + vector index adapter with a rebuild command.
   - [ ] Merge lexical + semantic results deterministically (document ranking rules).

## Open Questions (Need Decisions)

- Should the "main store" be one SQLite DB (preferred) vs multiple DBs (current chunks index is separate)?
- What is the default redaction policy for `quote` and chunk previews?
- How do we assign `case_id_norm` for documents that do not match any case?
- What is the minimum acceptable citation granularity: page-range only vs char spans?

