# UI V1 Acceptance Criteria (Deterministic)

This document defines pass/fail acceptance criteria for each required screen.
All criteria are deterministic and provenance-aware.

## Global Preconditions

- Case data is available for at least one `case_id_norm`.
- Read-model APIs follow `docs/ui/api_read_model_contracts.md`.
- UI routes follow `docs/ui/information_architecture.md`.
- Confidence and provenance components follow `docs/ui/component_contracts.md`.

## 1) Case Viewer (`/cases/:case_id`)

### Must Pass

1. Route resolves for valid `case_id`; invalid `case_id` returns deterministic `404` view.
2. Screen renders summary block, KPI row, key actors module, and recent evidence module.
3. KPI values match backend response fields exactly (`person_count`, `event_count`, `evidence_count`, `unresolved_date_count`).
4. Every displayed actor entry includes confidence and link to profile route.
5. Contradiction module renders count and source-linked entries when present.
6. Recent evidence list is paginated; no unbounded render.
7. Widget-level failure in one module preserves rendering of other modules.
8. All error messages shown in UI are redacted and include correlation ID.

### Determinism Checks

- Reloading with identical backend payload yields identical visual ordering.
- Unsupported query params are ignored and defaults are surfaced.

## 2) Profile Page (`/cases/:case_id/people/:person_id`)

### Must Pass

1. Route resolves for valid `person_id`; missing or invalid `person_id` returns deterministic error state.
2. Identity panel shows canonical name and alias list (if aliases exist).
3. Merge/dedupe indicator is visible when `merged=true`.
4. Facts list displays confidence + provenance badge for each fact.
5. Linked events and linked evidence sections are both present and independently paginated where required.
6. Evidence rows include document/page pointers when available.
7. Missing optional fields (`page`, `quote_redacted`) do not break rendering.
8. Empty states are explicit and non-error when no linked records exist.

### Determinism Checks

- Same `person_id` and same API payload produce stable ordering for facts/events/evidence.
- Confidence tags map thresholds consistently across reloads.

## 3) Timeline (`/cases/:case_id/timeline`)

### Must Pass

1. Screen separates rows into `normalized` and `unresolved` sections.
2. Normalized section ordering follows backend deterministic rank (`rank_key` / normalized date + tie-break).
3. Unresolved section displays unresolved reason for each item.
4. Row drilldown exposes source links (evidence/document/page) for each event.
5. Date/person/confidence filters apply and are URL-persisted.
6. Invalid date or filter params fall back to defaults without crashing.
7. Timeline remains usable when one lane fails (fail-soft behavior).
8. Sorting controls only expose allow-listed deterministic sort mode.

### Determinism Checks

- No event appears in both lanes simultaneously.
- Re-fetch with unchanged payload preserves row order exactly.

## 4) Evidence Browser (`/cases/:case_id/evidence`)

### Must Pass

1. Facet sidebar renders configured filters (`type`, `source`, date range, `confidence_min`, `tags`).
2. Results table is paginated and sortable only by allow-listed keys.
3. Applying/removing filters updates URL state and can be reversed.
4. Selected evidence opens detail/preview pane with provenance chain.
5. Empty result sets show explicit empty state (not generic error).
6. Errors from result fetch show redacted message + correlation ID.
7. Optional missing fields (e.g., `page=null`) render safe placeholder text.
8. Evidence claims shown in list/detail are source-linkable.

### Determinism Checks

- Same filter/sort/page tuple yields same result ordering for same backend payload.
- Unknown filter keys are ignored and do not alter known filter behavior.

## Cross-Screen Acceptance Criteria

1. Global navigation persists across case-scoped screens.
2. Shared filters (case/person/date/confidence) are URL-backed where applicable.
3. Focus lands on screen heading after route transition.
4. Keyboard traversal reaches all interactive controls.
5. Loading, empty, and error states exist for every major widget.
6. Provenance links are present for all key claims shown to analysts.
7. Telemetry events fire for view load, widget failures, filter apply, and provenance opens.

## Pass/Fail Rubric

- `PASS`: all must-pass criteria met with no critical accessibility or provenance gaps.
- `CONDITIONAL PASS`: non-critical cosmetic issues only; deterministic and provenance behavior intact.
- `FAIL`: any deterministic ordering violation, missing provenance on key claims, or unrecoverable screen crash.

## Manual Validation Commands (example)

1. `pytest -q tests/test_cli_*.py`
2. `pytest -q tests/test_phase11_knowledge_graph.py`
3. Run UI locally and verify routes:
   - `/cases/{case_id}`
   - `/cases/{case_id}/people/{person_id}`
   - `/cases/{case_id}/timeline`
   - `/cases/{case_id}/evidence`
