# Reusable Component Contracts (V1)

This document defines deterministic interfaces for shared UI components.
Contracts are read-model driven and provenance-first.

## Conventions

- All IDs are stable strings.
- `case_id` is required on all case-scoped components.
- Confidence values are normalized floats in `[0, 1]`.
- All components support explicit `loading`, `empty`, and `error` states.
- Error payloads must be redacted and include `correlation_id`.

## 1) Data Table

### Purpose

Render deterministic, paginated tabular data with allow-listed sorting.

### Props Contract

```ts
export type TableSortDir = "asc" | "desc";

export interface TableColumn<T> {
  key: string;
  header: string;
  accessor: (row: T) => string | number | null;
  sortable?: boolean;
  width?: string;
}

export interface TableState {
  loading: boolean;
  error?: { message_redacted: string; correlation_id: string };
  empty_label?: string;
}

export interface DataTableProps<T> {
  rows: T[];
  columns: TableColumn<T>[];
  page: number;
  page_size: number;
  total_rows: number;
  sort_key?: string;
  sort_dir?: TableSortDir;
  allowed_sort_keys: string[];
  on_page_change: (page: number) => void;
  on_sort_change: (sort_key: string, sort_dir: TableSortDir) => void;
  row_id: (row: T) => string;
  state: TableState;
}
```

### Behavioral Rules

- Reject unsupported sort keys by falling back to default sort.
- Stable row ordering for equal sort values uses row ID tie-break.
- Must not render unbounded row sets.

## 2) Faceted Filters

### Purpose

Provide reversible, composable filtering with URL-backed state.

### Props Contract

```ts
export interface FacetOption {
  value: string;
  label: string;
  count?: number;
}

export interface FacetDefinition {
  key: string;
  label: string;
  type: "multi_select" | "single_select" | "range";
  options?: FacetOption[];
  min?: number;
  max?: number;
}

export interface FacetedFiltersProps {
  facets: FacetDefinition[];
  selected: Record<string, string[] | [number, number] | null>;
  on_change: (next: Record<string, string[] | [number, number] | null>) => void;
  on_reset: () => void;
  url_sync: boolean;
  loading: boolean;
}
```

### Behavioral Rules

- Applying/removing filters is deterministic and reversible.
- Invalid values from URL are ignored and replaced with defaults.
- Component never mutates unsupported facet keys.

## 3) Entity Chip

### Purpose

Compact entity display used across timeline, profile, and evidence views.

### Props Contract

```ts
export interface EntityChipProps {
  entity_id: string;
  case_id: string;
  display_name: string;
  entity_type: "person" | "org" | "location" | "unknown";
  confidence?: number;
  alias_count?: number;
  merged?: boolean;
  href: string;
}
```

### Behavioral Rules

- Always render canonical display name.
- If `merged` true, show deterministic merge indicator.
- `alias_count` shown only when `> 0`.

## 4) Provenance Badge

### Purpose

Expose source traceability for any displayed claim.

### Props Contract

```ts
export interface ProvenanceRef {
  evidence_id: string;
  document_id: string;
  page?: number;
  quote_redacted?: string;
}

export interface ProvenanceBadgeProps {
  refs: ProvenanceRef[];
  max_visible?: number;
  on_open_ref: (ref: ProvenanceRef) => void;
}
```

### Behavioral Rules

- At least one reference required for non-derived claims.
- Missing `page` is allowed but must show as unknown page.
- Redacted quote strings are truncated to safe preview length.

## 5) Confidence Tag

### Purpose

Display normalized extraction confidence consistently.

### Props Contract

```ts
export interface ConfidenceThresholds {
  high_min: number;   // inclusive
  medium_min: number; // inclusive
}

export interface ConfidenceTagProps {
  value: number;
  thresholds: ConfidenceThresholds;
  show_numeric?: boolean;
}
```

### Behavioral Rules

- `value >= high_min` => high token.
- `value >= medium_min && value < high_min` => medium token.
- `value < medium_min` => low token.
- Out-of-range values clamp to `[0, 1]` and log redacted warning.

## 6) Timeline Row

### Purpose

Represent one event row with deterministic ordering metadata and source linkage.

### Props Contract

```ts
export interface TimelineRowModel {
  event_id: string;
  case_id: string;
  title: string;
  normalized_date?: string; // YYYY-MM-DD
  unresolved_reason?: "missing" | "conflict" | "partial";
  actor_entity_ids: string[];
  confidence?: number;
  provenance_refs: ProvenanceRef[];
}

export interface TimelineRowProps {
  row: TimelineRowModel;
  rank_key: string; // deterministic ordering key from backend
  on_open_event: (event_id: string) => void;
  on_open_source: (ref: ProvenanceRef) => void;
}
```

### Behavioral Rules

- Rows with `normalized_date` render in normalized lane.
- Rows without `normalized_date` render in unresolved lane.
- `rank_key` is authoritative for ordering within each lane.

## Shared State Contract

All reusable components should support this state union:

```ts
export type ComponentState =
  | { status: "loading" }
  | { status: "ready" }
  | { status: "empty"; message: string }
  | { status: "error"; message_redacted: string; correlation_id: string };
```

## Accessibility Baseline (for all components)

- Keyboard reachable in logical order.
- Focus visible via tokenized focus ring.
- Screen-reader labels for icon-only actions.
- Table headers and row actions include semantic roles.
