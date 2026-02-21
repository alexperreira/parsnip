# UI Implementation Plan

This document defines UI design rules and an execution plan derived from `docs/TODO_LONG_TERM.md` (L7: Optional UI).

## Objectives

- Build a practical analyst UI around four core capabilities:
  - Case viewer
  - Character profile pages
  - Interactive timelines
  - Evidence browser
- Keep behavior deterministic and auditable for investigative workflows.
- Start with local/single-node assumptions and preserve a clear upgrade path to large-scale features.

## Design Rules

### Product and UX rules

- Prioritize clarity over decoration; every screen must answer: "What happened, who is involved, and what supports this?"
- Keep navigation stable and predictable with persistent global sections: `Cases`, `People`, `Timeline`, `Evidence`.
- Support progressive disclosure: summary first, raw evidence and provenance one click away.
- Show confidence and source provenance near extracted facts.
- Make unknown or low-confidence data explicit; never silently infer.
- Keep filters composable and reversible (no hidden state).

### Data and behavior rules

- UI reads from canonical pipeline outputs; avoid UI-side business logic duplication.
- Every displayed entity/event must link back to one or more source documents/pages.
- Timeline ordering must use normalized timestamps when available, with explicit "unresolved date" grouping otherwise.
- Dedup/entity-merge state is first-class and visible in profiles.
- Fail-soft at page/widget level: partial data failure must not crash the full app view.

### Performance and reliability rules

- Use two-phase loading in UI:
  - lightweight list metadata first
  - deep detail fetch on demand
- Paginate evidence-heavy views; do not render unbounded lists.
- Define explicit loading, empty, and error states for every data panel.
- Instrument key metrics: view load time, query latency, filter application time, and per-widget failures.

### Security and privacy rules

- Treat all rendered text as untrusted input; sanitize and escape by default.
- Redact sensitive fields in logs and telemetry.
- Never expose raw document blobs by default; require explicit user action to open.
- Enforce role-aware access controls before cross-case or global search views.

## Delivery Plan

Use this checklist as the source of truth for implementation tracking.

## V1 Scope Decision (Locked)

- Product path: `L9 Option A — Investigator Tool`.
- Included: case summaries, key actors, chronologies, contradictions, and source-backed evidence navigation.
- Excluded for v1: semantic search system mode (Option B) and narrative engine mode (Option C).
- Scope boundary: single-case-first workflows with cross-case views only where provenance and access controls are explicit.

## Data Contracts (Locked to Current Pipeline)

Baseline source of truth: SQLite schema in `src/loaders/store.py` (`SCHEMA_VERSION = "6"`).  
UI v1 reads from existing tables only; no UI-owned derived persistence.

### Contract A: Entities and People

- Primary tables:
  - `entities(entity_id, entity, type, confidence, file_id, chunk_id, page_start, page_end, quote)`
  - `person_clusters(person_id, display_name, display_name_norm, dob)`
  - `person_cluster_members(person_id, obs_id)`
  - `person_observations(obs_id, name, name_norm, file_id, chunk_id, page_start, page_end)`
  - `person_resolution_edges(left_obs_id, right_obs_id, decision, score, reasons_json)`
- UI guarantees:
  - Profile pages resolve people through `person_clusters.person_id`.
  - Aliases come from joined `person_observations.name` values for cluster members.
  - Merge rationale is surfaced from `person_resolution_edges.decision/reasons_json`.

### Contract B: Timeline Events

- Primary tables:
  - `events(event_id, event, date, confidence, file_id, chunk_id, page_start, page_end, quote)`
  - `event_times(event_id, date_raw, date_start, date_end, precision, status, parser, anchor_date, notes_json)`
  - `event_cases(event_id, case_id, case_id_norm, source)`
- Ordering rule (deterministic):
  - Sort by `event_times.date_start` ascending for rows where `status='ok'`.
  - Group unresolved rows (`status IN {'unresolved_relative','missing_anchor','invalid_format','unresolved_ambiguous','empty'}`) in a separate explicit section.
  - Tie-breaker: `events.event_id ASC`.
- Case scoping rule:
  - Use `event_cases.case_id_norm` as the case filter key.
  - If source is fallback, keep `file:{file_id}` key visible as fallback-case marker.

### Contract C: Evidence Metadata

- Evidence rows for UI provenance must include:
  - `file_id`
  - `chunk_id`
  - `page_start`
  - `page_end`
  - `confidence` (nullable)
  - `quote` (nullable, redacted/truncated in list views)
- Current evidence sources in v1:
  - Extraction tables: `entities`, `events`, `conversations`, `identity_signals`.
  - Relationship evidence: `kg_edge_evidence(src_type, src_id, edge_type, dst_type, dst_id, file_id, chunk_id, page_start, page_end, confidence, source_phase, extractor_version, created_utc)`.
- Provenance link contract:
  - Every UI claim card must carry an evidence pointer tuple:
    - `(file_id, chunk_id, page_start, page_end, source_phase_or_table, record_id)`

### Contract D: Cases and Navigation Keys

- Cases are keyed by normalized identifier:
  - `cases(case_id_norm, case_id_display, sources_json)` when available.
  - `event_cases.case_id_norm` is always available for timeline scoping.
- URL-state contract keys:
  - `case_id_norm`
  - `person_id`
  - `date_start` / `date_end`
  - `confidence_min`

### Contract E: Fail-Soft Behavior

- If a required table is missing, the affected widget renders an error state with table name and continues rendering remaining widgets.
- If optional tables (`cases`, `kg_edge_evidence`, threading tables) are absent, hide dependent modules without failing the page.
- Null/empty `quote` values are valid and must not block evidence rendering.

## Parallel Workstream (can be done by another agent now)

- [x] Define UI information architecture (routes, top-level nav, shared layout regions).
- [x] Produce low-fidelity wireframes for: case viewer, profile page, timeline, evidence browser.
- [x] Define design tokens (color, typography, spacing, elevation, focus states).
- [x] Specify reusable component contracts (table, faceted filters, entity chip, provenance badge, confidence tag, timeline row).
- [x] Draft API contract for read models required by each screen (including pagination, sorting, filtering params).
- [ ] Define observability schema for UI telemetry and redacted error reporting.
- [ ] Create accessibility checklist (keyboard flow, focus order, contrast, screen-reader labels).
- [ ] Write acceptance criteria per screen with deterministic expected behavior.

## Sequential Workstream (must be completed in order)

- [x] Confirm product path and v1 scope boundaries (investigator-first baseline from L9 option A).
- [x] Lock data contracts against current pipeline outputs (entities, timeline events, evidence metadata).
- [x] Implement app shell and navigation skeleton with route guards and error boundaries.
- [x] Implement case viewer with summary + evidence counts + linked entities.
- [ ] Implement character profile page with alias visibility and linked events/evidence.
- [ ] Implement interactive timeline with normalized/unresolved date handling and source drill-down.
- [ ] Implement evidence browser with faceted filtering, pagination, and document-page provenance links.
- [ ] Integrate dedupe and conversation-thread indicators where available from backend outputs.
- [ ] Add cross-screen shared filters (case, person, date range, confidence) with URL-state persistence.
- [ ] Add loading/empty/error states and fail-soft widget-level recovery behavior.
- [ ] Add instrumentation dashboards for UI performance and failure metrics.
- [ ] Run end-to-end validation on representative case sets and document known gaps.

## Definition of Done (UI v1)

- [ ] All four core screens are functional with linked provenance.
- [ ] Users can trace every key claim to source evidence.
- [ ] Timeline behavior is deterministic for normalized and unresolved dates.
- [ ] Entity aliases/dedup state is visible and navigable.
- [ ] No critical accessibility violations in the agreed checklist.
- [ ] Telemetry and redacted error reporting are enabled.
- [ ] v1 limitations are documented with explicit follow-up items.

## Out of Scope for Initial UI v1

- Distributed/multi-machine orchestration controls.
- Full graph-database-native visualization layer.
- Automated narrative generation UI beyond basic summaries.
