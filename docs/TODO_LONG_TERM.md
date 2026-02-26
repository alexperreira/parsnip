# TODO: Long-Term Goals (Strategic Roadmap)

This file defines _where the project is heading_.
DO NOT implement these items until instructed.

---

## L1 — Scale to Millions of Documents

- Replace gzip with zstandard (.zst) for better compression.
- Introduce parallel sharding workers.
- Add distributed processing option (multiple machines).
- Add progress dashboards + throughput metrics.

---

## L2 — Smarter Mixed-PDF Handling

- For "mixed" PDFs:
  - Extract text where available.
  - OCR only image-heavy pages.
- Add per-page quality scoring.
- Status update (2026-02-19): implemented in the active pipeline; see `docs/MIXED_PDF.md` for rollout details.

---

## L3 — Entity Resolution (Deduplication)

Build logic to:

- Merge duplicate people across files.
- Handle aliases (e.g., "Robert Smith" vs "Bob Smith").
- Link identities via shared attributes (DOB, case ID, address).

---

## L4 — Timeline Stitching

- Normalize dates into a global timeline.
- Handle relative dates ("last Tuesday").
- Build per-case and cross-case timelines.

---

## L5 — Conversation Threading

- Group related dialogues across multiple documents.
- Detect recurring participants.
- Label topics automatically.

---

## L6 — Knowledge Graph

- Move from SQLite tables to a graph store.
- Represent relationships between people, events, and cases.
- Storage staging note: see `docs/GRAPH_DB_DECISION.md`.

---

## L7 — Optional UI (Later)

If you choose to add a UI:

- Case viewer
- Character profile pages
- Interactive timelines
- Evidence browser

---

## L8 — Compute Strategy

- Triage first:
  - Only send high-signal documents to LLM.
- Add keyword + NER pre-filters.
- Consider fine-tuning a smaller model for extraction.
- Status update (2026-02-26): compute-strategy routing is active in the pipeline (triage + budgets + route-aware LLM filtering + optional `--llm-small-model`/`--llm-large-model` overrides), and the remaining L8 checklist items in `docs/COMPUTE_STRATEGY.md` are now complete.

---

## L9 — Product Paths (Choose One Later)

Option A: Investigator Tool

- Case summaries
- Key actors
- Chronologies
- Contradictions

Option B: Search System

- Semantic search across millions of docs
- Filter by person, date, case, or topic

Option C: Narrative Engine

- Auto-generated character profiles
- Storylines per case
- Relationship maps
