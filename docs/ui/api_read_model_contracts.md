# API Contract: UI Read Models (V1)

This contract defines deterministic read endpoints for UI screens.
All endpoints are read-only and case-scoped unless explicitly global.

## Global Rules

- Response format: JSON
- Time format: ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SSZ`)
- Pagination model: `page` (1-based), `page_size`, `total`, `has_next`
- Sorting model: allow-listed `sort_by` and `sort_dir` (`asc|desc`)
- Confidence values: float in `[0,1]`
- Error payloads are redacted and include `correlation_id`

### Common Envelope

```json
{
  "data": {},
  "meta": {
    "request_id": "req_123",
    "page": 1,
    "page_size": 50,
    "total": 240,
    "has_next": true
  },
  "error": null
}
```

### Error Envelope

```json
{
  "data": null,
  "meta": {
    "request_id": "req_123"
  },
  "error": {
    "code": "INVALID_FILTER",
    "message_redacted": "Invalid query parameter",
    "correlation_id": "corr_abc"
  }
}
```

## 1) Case Index

### `GET /api/v1/cases`

Returns lightweight case list metadata.

Query params:

- `page` (default `1`)
- `page_size` (allowed: `25|50|100`, default `50`)
- `status` (optional)
- `updated_start` / `updated_end` (optional date)
- `sort_by` (allowed: `updated_at|case_id_norm`, default `updated_at`)
- `sort_dir` (`asc|desc`, default `desc`)

`data` shape:

```json
{
  "cases": [
    {
      "case_id_norm": "case_2026_001",
      "case_id_display": "CASE-2026-001",
      "title": "Procurement Review",
      "status": "active",
      "updated_at": "2026-02-20T21:14:22Z",
      "event_count": 87,
      "person_count": 12,
      "evidence_count": 430
    }
  ]
}
```

## 2) Case Viewer Read Model

### `GET /api/v1/cases/{case_id}/summary`

Returns summary, KPIs, key actors, and contradiction counters.

`data` shape:

```json
{
  "case": {
    "case_id_norm": "case_2026_001",
    "case_id_display": "CASE-2026-001",
    "title": "Procurement Review",
    "last_ingest_at": "2026-02-20T21:14:22Z",
    "synopsis": "Short deterministic summary"
  },
  "kpis": {
    "person_count": 12,
    "event_count": 87,
    "evidence_count": 430,
    "unresolved_date_count": 9
  },
  "key_actors": [
    {
      "person_id": "p_0031",
      "display_name": "Jane Doe",
      "role": "approver",
      "confidence": 0.78
    }
  ],
  "contradictions": {
    "count": 2
  }
}
```

## 3) People Index

### `GET /api/v1/cases/{case_id}/people`

Query params:

- `page`, `page_size`
- `confidence_min` (optional)
- `sort_by` (allowed: `display_name|confidence|event_count`, default `display_name`)
- `sort_dir` (`asc|desc`, default `asc`)

`data` shape:

```json
{
  "people": [
    {
      "person_id": "p_0031",
      "display_name": "Jane Doe",
      "alias_count": 2,
      "merged": true,
      "confidence": 0.78,
      "event_count": 13,
      "evidence_count": 31
    }
  ]
}
```

## 4) Profile Read Model

### `GET /api/v1/cases/{case_id}/people/{person_id}`

`data` shape:

```json
{
  "person": {
    "person_id": "p_0031",
    "display_name": "Jane Doe",
    "aliases": ["J. Doe", "Janet D."],
    "merged": true,
    "merge_members": ["p_0091", "p_0112"]
  },
  "facts": [
    {
      "fact_id": "f_101",
      "label": "Employment",
      "value": "Director",
      "confidence": 0.78,
      "provenance": [
        {"evidence_id": "ev_22", "document_id": "doc_71", "page": 4}
      ]
    }
  ]
}
```

### `GET /api/v1/cases/{case_id}/people/{person_id}/events`

Query params: `page`, `page_size`, `sort_by` (`normalized_date|event_id`), `sort_dir`

### `GET /api/v1/cases/{case_id}/people/{person_id}/evidence`

Query params: `page`, `page_size`, `sort_by` (`doc_id|page|confidence`), `sort_dir`

## 5) Timeline Read Model

### `GET /api/v1/cases/{case_id}/timeline`

Query params:

- `page`, `page_size`
- `date_start`, `date_end` (optional)
- `person_id` (optional)
- `confidence_min` (optional)
- `lane` (`normalized|unresolved|all`, default `all`)
- `sort_by` (fixed allow-list: `normalized_date,event_id`)
- `sort_dir` (`asc|desc`, default `asc`)

`data` shape:

```json
{
  "normalized": [
    {
      "event_id": "e_0102",
      "title": "Approval submitted",
      "normalized_date": "2025-03-01",
      "rank_key": "2025-03-01|e_0102",
      "confidence": 0.81,
      "actor_entity_ids": ["p_0031"],
      "provenance": [
        {"evidence_id": "ev_91", "document_id": "doc_7", "page": 4}
      ]
    }
  ],
  "unresolved": [
    {
      "event_id": "e_0991",
      "title": "Conversation excerpt",
      "unresolved_reason": "missing",
      "rank_key": "unresolved|e_0991",
      "confidence": 0.63,
      "actor_entity_ids": ["p_0012"],
      "provenance": [
        {"evidence_id": "ev_112", "document_id": "doc_44", "page": 1}
      ]
    }
  ]
}
```

## 6) Evidence Browser Read Model

### `GET /api/v1/cases/{case_id}/evidence`

Query params:

- `page`, `page_size`
- `type` (multi)
- `source` (multi)
- `date_start`, `date_end`
- `confidence_min`
- `tags` (multi)
- `sort_by` (allowed: `doc_id|page|confidence|ingested_at`, default `ingested_at`)
- `sort_dir` (`asc|desc`, default `desc`)

`data` shape:

```json
{
  "facets": {
    "type": [{"value": "pdf", "count": 220}],
    "source": [{"value": "email", "count": 145}],
    "tags": [{"value": "finance", "count": 44}]
  },
  "results": [
    {
      "evidence_id": "ev_001",
      "document_id": "doc_7",
      "page": 4,
      "type": "pdf",
      "source": "email",
      "confidence": 0.91,
      "tags": ["finance"],
      "quote_redacted": "Invoice approved for...",
      "linked_claim_count": 3,
      "ingested_at": "2026-02-20T21:14:22Z"
    }
  ]
}
```

### `GET /api/v1/cases/{case_id}/evidence/{evidence_id}`

Returns full evidence detail for selected row, including provenance chain and linked entities/events.

## Deterministic Validation Rules

- Unknown `sort_by` value => fallback to endpoint default, reported in `meta.applied_defaults`.
- Out-of-range `page_size` => clamp to nearest allowed value.
- Invalid date filters => ignore and surface in `meta.applied_defaults`.
- Unknown filter keys => ignored; never produce 500.

## Optional Fields and Fail-Soft Behavior

- `page` can be null for non-paginated sources.
- `quote_redacted` can be null.
- Missing optional modules (dedupe/threading) return empty arrays, not errors.
- Partial backend failures return data for successful widgets plus per-widget redacted errors.
