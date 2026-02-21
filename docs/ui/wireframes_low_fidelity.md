# Low-Fidelity Wireframes (V1)

These wireframes define deterministic layout structure for four required screens.
They are intentionally low-fidelity and text-first.

## 1) Case Viewer (`/cases/:case_id`)

```text
+--------------------------------------------------------------------------------+
| Header: Parsnip | Case Selector [Case A] | Role: Analyst | Last Updated        |
+--------------------------------------------------------------------------------+
| Nav: [Cases] [People] [Timeline] [Evidence]                                   |
+--------------------------------------------------------------------------------+
| Context: Case Viewer | Case ID: CASE-2026-001 | Filters: Date [..] Conf [..]   |
+--------------------------------------------------------------------------------+
| Summary Card                  | KPI Strip                                      |
| - case title                  | People: 12 | Events: 87 | Evidence: 430        |
| - short synopsis              | Unresolved Dates: 9                            |
+-------------------------------+------------------------------------------------+
| Key Actors Table              | Contradictions Panel                           |
| name | role | confidence      | - Conflict 1 (link evidence)                   |
| ...                           | - Conflict 2 (link evidence)                   |
+-------------------------------+------------------------------------------------+
| Recent Evidence (paginated list)                                               |
| id | type | source | page | linked claim | confidence                          |
+--------------------------------------------------------------------------------+
| Widget Errors (if any): [redacted error + correlation id]                      |
+--------------------------------------------------------------------------------+
```

## 2) Character Profile (`/cases/:case_id/people/:person_id`)

```text
+--------------------------------------------------------------------------------+
| Header + Nav (same global shell)                                               |
+--------------------------------------------------------------------------------+
| Context: Person Profile | Case: CASE-2026-001 | Person: P-0031                 |
+--------------------------------------------------------------------------------+
| Identity Card                    | Merge/Dedupe State                           |
| Canonical: Jane Doe              | Status: Merged canonical                     |
| Aliases: J. Doe; Janet D.        | Linked records: 4                            |
+----------------------------------+----------------------------------------------+
| Facts + Confidence + Provenance                                                  |
| - Employment claim [0.78] [source: DOC-71 p4]                                  |
| - Address claim    [0.62] [source: DOC-12 p1]                                  |
+--------------------------------------------------------------------------------+
| Linked Events Table                                                            |
| date_norm | event type | role | evidence count                                 |
+--------------------------------------------------------------------------------+
| Linked Evidence List (paginated)                                                |
| evidence id | doc | page | quote snippet (truncated/redacted)                 |
+--------------------------------------------------------------------------------+
```

## 3) Timeline (`/cases/:case_id/timeline`)

```text
+--------------------------------------------------------------------------------+
| Header + Nav (same global shell)                                               |
+--------------------------------------------------------------------------------+
| Context: Timeline | Case: CASE-2026-001 | Filters: Actor [..] Date [..]       |
+--------------------------------------------------------------------------------+
| Controls: Sort = Deterministic(normalized_date asc, tie-break id asc)          |
+--------------------------------------------------------------------------------+
| Normalized Date Lane                                                            |
| 2025-03-01 | Event E-0102 | actors: A,B | confidence 0.81 | [view sources]    |
| 2025-03-02 | Event E-0103 | actors: C   | confidence 0.74 | [view sources]    |
| ...                                                                            |
+--------------------------------------------------------------------------------+
| Unresolved Date Lane (explicit grouping)                                        |
| Unknown date | Event E-0991 | reason: missing timestamp | [view sources]       |
| Unknown date | Event E-0998 | reason: conflicting dates | [view sources]       |
+--------------------------------------------------------------------------------+
| Row Detail Drawer (opens on select)                                             |
| event summary | entities | source links (doc/page) | redacted errors if present |
+--------------------------------------------------------------------------------+
```

## 4) Evidence Browser (`/cases/:case_id/evidence`)

```text
+--------------------------------------------------------------------------------+
| Header + Nav (same global shell)                                               |
+--------------------------------------------------------------------------------+
| Context: Evidence Browser | Case: CASE-2026-001 | Shared filters               |
+--------------------------------------------------------------------------------+
| Facets Sidebar         | Results Table (paginated + deterministic sort)        |
| type [ ]               | evidence id | type | source | page | conf | tags      |
| source [ ]             | ----------------------------------------------------  |
| date band [ ]          | EV-001      | pdf  | DOC-7  | 4    | .91  | finance   |
| confidence [ ]         | EV-002      | msg  | DOC-9  | 1    | .73  | meeting   |
| tags [ ]               | ...                                                 |
+------------------------+-------------------------------------------------------+
| Evidence Preview/Detail Pane                                                   |
| selected evidence summary | provenance chain | open document/page action       |
+--------------------------------------------------------------------------------+
| Empty State: "No evidence matches current filters"                             |
| Error State: redacted message + correlation id                                  |
+--------------------------------------------------------------------------------+
```

## Interaction Notes (applies to all wireframes)

- Global shell is stable across screens.
- Shared filters are URL-backed and reversible.
- Pagination is required for evidence-heavy lists.
- Low-confidence or unresolved data is explicitly labeled.
- Every key claim has visible source links (document + page).
