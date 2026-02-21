# UI Information Architecture (V1)

This document defines deterministic screen structure for the investigator-first UI path in `docs/UI.md`.

## Route Map

All routes are case-aware and provenance-first. Unknown identifiers return deterministic `404` state.

- `/cases`
  - Case index with metadata-only list rows.
  - Supports pagination and lightweight filters (status, updated range).
- `/cases/:case_id`
  - Case viewer summary.
  - Linked panels: key actors, event counts, evidence counts, contradiction flags.
- `/cases/:case_id/people`
  - People index for a case.
  - Sortable by canonical name, confidence, event count.
- `/cases/:case_id/people/:person_id`
  - Character profile with aliases, linked events, linked evidence.
- `/cases/:case_id/timeline`
  - Timeline view with normalized-date ordering and unresolved-date section.
- `/cases/:case_id/evidence`
  - Evidence browser with faceted filters and pagination.
- `/cases/:case_id/evidence/:evidence_id`
  - Evidence detail with provenance and document/page links.

## Global Navigation

Primary persistent navigation (left rail on desktop, bottom tabs on mobile):

- `Cases` -> `/cases`
- `People` -> `/cases/:case_id/people`
- `Timeline` -> `/cases/:case_id/timeline`
- `Evidence` -> `/cases/:case_id/evidence`

Rules:

- `People`, `Timeline`, and `Evidence` are disabled until a case is selected.
- Case context is always visible in header (`case title`, `case id`, `last updated`).
- Route transitions preserve current case when destination is case-scoped.

## Shared Layout Regions

Each case-scoped screen follows a stable layout:

1. Global shell
   - Header: app identity, active case selector, access/role indicator.
   - Primary nav: persistent sections listed above.
2. Context bar
   - Screen title and short deterministic subtitle.
   - Global case-scoped filters (date range, confidence, person) as URL state.
3. Content region
   - Main panel for screen-specific data.
   - Optional side panel for provenance and selected-item detail.
4. System feedback region
   - Inline loading, empty, and error states per widget.
   - Non-blocking error summary strip when partial widgets fail.

## Screen-Level Region Contracts

### Case Viewer (`/cases/:case_id`)

- Summary header (case facts, ingest timestamp).
- KPI row (people count, event count, evidence count, unresolved-date count).
- Linked entities panel.
- Recent evidence panel (paginated).

### Profile (`/cases/:case_id/people/:person_id`)

- Identity panel (canonical name, aliases, merge status).
- Confidence/provenance tags near each derived fact.
- Linked events table.
- Linked evidence list with document/page pointers.

### Timeline (`/cases/:case_id/timeline`)

- Control bar (sort fixed to deterministic mode, date filters, actor filter).
- Normalized-date lane (strict ordered rows).
- Unresolved-date lane (explicit grouped section, no implicit ordering claims).
- Row detail drawer with source links.

### Evidence Browser (`/cases/:case_id/evidence`)

- Facet sidebar (type, source, date band, confidence band, tags).
- Results table (paginated, sortable by deterministic fields only).
- Preview/detail pane with provenance.

## Route State and URL Rules

- Filter state is URL-encoded and reversible.
- Invalid filter params are ignored with deterministic defaults; UI shows defaults.
- Pagination params are bounded (`page >= 1`, `page_size` from allow-list).
- Sorting is allow-listed per screen; unsupported sorts fall back to default and are surfaced.

## Access and Fail-Soft Rules

- Unauthorized route access renders deterministic `403` with no leaked data.
- Missing resources render deterministic `404` with case-safe messaging.
- Widget data failures do not collapse the entire screen.
- All errors shown to users are redacted and carry correlation IDs only.
