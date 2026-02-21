# UI Observability Schema (V1)

This schema defines telemetry and redacted error reporting for the UI.
It is deterministic, privacy-preserving, and case-aware.

## Objectives

- Measure UI performance and reliability by screen/widget.
- Capture filter/query behavior without leaking sensitive content.
- Track failures with correlation IDs and redacted payloads.
- Support fail-soft diagnosis where one widget fails but screen remains usable.

## Event Envelope

All telemetry events share this envelope:

```json
{
  "event_name": "ui.view.loaded",
  "event_version": 1,
  "occurred_at": "2026-02-21T10:00:00Z",
  "session_id": "sess_abc",
  "request_id": "req_123",
  "correlation_id": "corr_456",
  "user_role": "analyst",
  "case_id_norm": "case_2026_001",
  "route": "/cases/:case_id/timeline",
  "payload": {}
}
```

### Field Rules

- `session_id`, `request_id`, `correlation_id` are opaque identifiers.
- `route` must use route templates, not raw URL values.
- `user_role` is role label only; no user PII fields.
- `payload` keys are allow-listed by event type.

## Telemetry Event Types

## 1) View Load Events

### `ui.view.load_started`

Payload:

```json
{
  "view": "timeline"
}
```

### `ui.view.loaded`

Payload:

```json
{
  "view": "timeline",
  "duration_ms": 412,
  "widgets_total": 5,
  "widgets_failed": 1,
  "partial_failure": true
}
```

### `ui.view.load_failed`

Payload:

```json
{
  "view": "timeline",
  "duration_ms": 389,
  "error_code": "UPSTREAM_TIMEOUT",
  "error_message_redacted": "Request timed out"
}
```

## 2) Widget Lifecycle Events

### `ui.widget.load_started`

Payload:

```json
{
  "view": "case_viewer",
  "widget": "recent_evidence"
}
```

### `ui.widget.loaded`

Payload:

```json
{
  "view": "case_viewer",
  "widget": "recent_evidence",
  "duration_ms": 127,
  "row_count": 50,
  "empty": false
}
```

### `ui.widget.failed`

Payload:

```json
{
  "view": "case_viewer",
  "widget": "recent_evidence",
  "error_code": "TABLE_MISSING",
  "error_message_redacted": "Missing dependency table",
  "fallback_applied": true
}
```

## 3) Filter and Query Interaction Events

### `ui.filter.applied`

Payload:

```json
{
  "view": "evidence_browser",
  "filter_keys": ["type", "confidence_min"],
  "filter_count": 2,
  "url_state_synced": true,
  "apply_duration_ms": 94
}
```

### `ui.filter.reset`

Payload:

```json
{
  "view": "evidence_browser",
  "cleared_filter_count": 4
}
```

### `ui.query.executed`

Payload:

```json
{
  "view": "timeline",
  "endpoint": "/api/v1/cases/{case_id}/timeline",
  "query_key_count": 4,
  "query_duration_ms": 203,
  "http_status": 200,
  "response_size_bytes": 28412
}
```

## 4) Pagination and Sorting Events

### `ui.pagination.changed`

Payload:

```json
{
  "view": "evidence_browser",
  "widget": "results_table",
  "page": 3,
  "page_size": 50
}
```

### `ui.sort.changed`

Payload:

```json
{
  "view": "people_index",
  "widget": "people_table",
  "sort_by": "confidence",
  "sort_dir": "desc",
  "defaulted": false
}
```

## 5) Provenance and Drilldown Events

### `ui.provenance.opened`

Payload:

```json
{
  "view": "timeline",
  "event_id": "e_0102",
  "evidence_id": "ev_91",
  "document_id": "doc_7",
  "page": 4
}
```

### `ui.evidence.opened`

Payload:

```json
{
  "view": "evidence_browser",
  "evidence_id": "ev_001",
  "has_quote_preview": true
}
```

## Metrics Derivation

Required metric families:

- `ui_view_load_ms{view}`: p50/p95/p99
- `ui_widget_load_ms{view,widget}`: p50/p95
- `ui_query_latency_ms{view,endpoint}`: p50/p95/p99
- `ui_filter_apply_ms{view}`: p50/p95
- `ui_widget_failures_total{view,widget,error_code}`
- `ui_view_partial_failures_total{view}`
- `ui_empty_state_total{view,widget}`

## Redaction Policy

Never emit raw source text, quotes, or extracted claims in telemetry.

### Forbidden in telemetry payloads

- Raw document text or snippets over redaction threshold.
- Filenames/paths from local filesystem.
- User identifiers, emails, phone numbers, account IDs.
- Free-form query text from analyst input.

### Allowed (sanitized)

- Opaque IDs: `case_id_norm`, `event_id`, `evidence_id`, `document_id`.
- Enum labels and numeric metrics.
- Redacted error messages from allow-list.

## Error Schema

UI error reports must use this shape:

```json
{
  "error_code": "UPSTREAM_TIMEOUT",
  "error_message_redacted": "Request timed out",
  "severity": "warning",
  "view": "timeline",
  "widget": "timeline_rows",
  "correlation_id": "corr_456",
  "request_id": "req_123",
  "retryable": true
}
```

### Severity levels

- `info`: recoverable non-impacting state.
- `warning`: widget-level failure with fail-soft fallback.
- `error`: view-level failure preventing primary task completion.

## Sampling and Delivery

- `ui.view.*` events: 100% sample rate.
- `ui.widget.*` events: 100% for failures, 20% for successful loads.
- `ui.query.executed`: 25% success, 100% non-2xx responses.
- Batch flush interval: 5s or 50 events (whichever first).
- On unload, flush best-effort with 200ms timeout.

## Data Retention

- Raw telemetry events: 14 days.
- Aggregated metrics: 90 days.
- Error summary rollups: 90 days.
- Correlation ID lookup index: 30 days.

## Validation Rules

- Unknown event names are dropped.
- Events missing required envelope fields are dropped and counted.
- Payload keys not on event allow-list are removed.
- Redaction validator runs before emit; blocked events increment `ui_telemetry_redaction_block_total`.
