# Compute Strategy (L8)

Goal: minimize expensive compute (OCR + LLM) while keeping recall high for “case-relevant” content.

This is an implementation plan for `docs/TODO_LONG_TERM.md` item **L8 — Compute Strategy**. It is not a request to implement the work yet.

## Principles (repo-aligned)

- **Triage-first:** run cheap, deterministic passes before any LLM calls.
- **Two-phase pipeline:** (1) enumerate + metadata/signals, (2) deep parsing only when justified.
- **Fail-soft + idempotent:** a bad file/chunk never crashes the run; reruns reuse cached work.
- **Observability without leakage:** track counts, timings, and hashes; avoid logging raw text by default.

## Current baseline (today)

- Phase 3 emits normalized text outputs under `output/text/`.
- Phase 4 produces `output/text/chunks.jsonl`.
- Phase 5 runs LLM extraction over all chunks by default; when triage is enabled, extraction runs on selected chunk streams.
- Phase 7 reports yield + invalid JSON rate metrics.

Compute Strategy adds a new decision layer between **chunking** and **LLM**.

## Proposed architecture: routing-by-chunk

Introduce a “triage” stage that reads `chunks.jsonl` and produces:

1) `triage.jsonl` (one record per chunk) with scores, reasons, and the chosen route.
2) optionally, derived chunk streams for the next stage (filtered subsets).

### Chunk routes (deterministic)

Each chunk gets a `route`:

- `skip`: do not send to LLM; record why.
- `rules_only`: run only non-LLM extractors (regex/date/phone/address/etc.).
- `llm_small`: send to the cheaper/smaller model/prompt set.
- `llm_large`: send to the more capable model/prompt set (rare).
- `review`: ambiguous / low-quality; only used if an operator opts in.

These routes are implementation details, but the key is: **not all chunks go to the LLM stage**.

## Stage 1: lightweight signals (no third-party deps)

For each chunk:

- [x] Compute **text quality** features: length, non-whitespace ratio, OCR artifacts (e.g., high punctuation rate), repeated characters, word-shape diversity.
- [x] Compute **structure hint** features: dialogue markers, bullet density, table-ish patterns, presence of timestamps.
- [x] Compute **entity-ish hint** features (rules): honorifics (`Mr.`, `Dr.`), capitalized-name sequences, initials, common org suffixes, badge/ID patterns.
- [x] Compute **event-ish hint** features (rules): date patterns, time-of-day, “on/at/by” + date proximity, incident verbs (“arrested”, “interviewed”, “reported”).
- [x] Add **domain keyword** features with configurable keyword lists (case-specific + general).

Output: a `features` object plus derived `score` and `reasons[]`.

## Stage 2: keyword + NER prefilters (optional, pluggable)

### 2A) Keyword filtering (always available)

- [x] Maintain curated keyword packs:
  - `people_identity` (DOB, SSN, address, alias, etc.)
  - `events_timeline` (date, time, “responded”, “observed”, etc.)
  - `communications` (“text message”, “call”, “voicemail”, speaker tags)
  - `legal` (charges, statute, warrant, affidavit, etc.)
- [x] Support user-provided packs via a directory of `.txt` files.
- [x] Compile to case-insensitive regex with word boundaries where safe.

### 2B) NER filtering (only if installed; off by default)

If a fast local NER library is available (e.g., spaCy), use it only to boost triage:

- [x] Count entities by type (PERSON/ORG/GPE/DATE).
- [x] Persist only aggregate counts by default; gate raw spans behind an explicit flag.

If NER is missing, triage still works via rule-based features.

## Stage 3: scoring, thresholds, and budgets

### Scoring model (v1)

Use a deterministic weighted sum (or calibrated logistic) over features:

- keyword hits (weighted by pack)
- strong patterns (DOB/SSN/phone/date/time/address)
- dialogue density (for conversation extraction)
- quality score gates (very low-quality chunks usually `skip` unless they hit strong patterns)

### Global budget controls (hard limits)

Add knobs to prevent runaway cost:

- [x] Add `max_llm_chunks` (cap total LLM calls; CLI flag later).
- [x] Add `max_llm_chunks_per_file` (CLI flag later).
- [x] Add `max_llm_tokens` (rough estimate via char/token heuristic; CLI flag later).
- [x] Add `allow_file_ids` / `deny_file_ids` budgets (CLI flag later).

When budget is exceeded, remaining chunks are deterministically deprioritized by `(score, tie_breaker)` where `tie_breaker` is stable (e.g., `chunk_id`).

## Stage 4: selective LLM extraction

Update the LLM stage to accept a chunk stream to process (rather than assuming “all chunks”):

- [x] v1: emit `chunks.llm_small.jsonl` / `chunks.llm_large.jsonl` (subset streams) and point the existing LLM extractors at the selected stream.
- [x] v2: pass a `triage.jsonl` alongside `chunks.jsonl` and let the LLM stage internally select by `route`.

For multi-model routing:

- [x] Route `llm_small` to the faster/cheaper model for high-confidence “easy” chunks.
- [ ] Reserve `llm_large` for:
  - [x] low-quality but high-value chunks
  - [ ] complex narrative sections
  - [ ] chunks that failed `llm_small` (invalid JSON / refusal / empty extraction)

## Stage 5: caching and reruns (critical for cost)

Cache key:

- [x] Add `chunk_text_hash`: hash of normalized chunk text (normalize whitespace + NFC + strip nulls).
- [x] Include `extractor_version` in the cache key (prompt + schema + model + provider).

Store:

- [x] Store per-extractor output JSONL keyed by `(chunk_id, chunk_text_hash, extractor_version)`.
- [x] Store “result status” so failed calls don’t retry endlessly without an operator override.

This enables:

- resume after interruptions
- incremental processing when only a subset changes
- deterministic, explainable reruns

## Fine-tuning path (later; optional)

Prefer starting with a **classifier for routing**, not a full extractor:

- [ ] Collect training labels from:
   - [x] existing LLM outputs (yield vs empty)
   - [x] operator “review” decisions
   - [ ] downstream resolver/timeline utility signals
- [x] Train a lightweight model to predict `route` or “send_to_llm” probability.
- [x] Keep a deterministic fallback and retain budget caps.

Only consider fine-tuning an extractor if:

- schemas are stable,
- you have enough curated examples,
- and you can measure improvements vs prompt-only baselines.

## Data contracts (proposed)

### `triage.jsonl` record (one per chunk)

- `file_id`, `chunk_id`, `page_range`
- `chunk_text_hash`
- `features` (counts/booleans only by default)
- `score` (float 0..1)
- `route` (`skip|rules_only|llm_small|llm_large|review`)
- `reasons` (short strings; redact any text fragments)
- `timing_ms` (optional)

### Derived chunk streams (optional)

- `chunks.llm_small.jsonl`
- `chunks.llm_large.jsonl`

These files contain the original chunk records, unchanged, but filtered by route.

## Metrics and validation additions

Add Phase 7 metrics that make triage measurable:

- [x] Report `% chunks routed to LLM` and breakdown by route.
- [x] Report yield per routed chunk (entities/events/conversations).
- [x] Report yield per 1k chunks scanned (end-to-end).
- [x] Report invalid JSON rate by route/model.
- [x] Report time per stage (triage/llm/load/validate).

All metrics should be computable without storing raw chunk text in logs.

## CLI integration plan

- [x] Add `fileparse triage` command (input: `output/text/chunks.jsonl`, output: `output/triage.jsonl` + optional filtered chunk files).
- [x] Add `triage` to `fileparse run --steps ...` between `chunk` and `llm`.
- [x] Add `--triage-*` flags for thresholds, keyword packs, and budgets.

## Test plan (deterministic, no LLM required)

- [x] Add unit tests for:
  - feature extraction (keyword hits, pattern detection)
  - scoring determinism + stable tie-breaks
  - budget cap behavior
  - `triage.jsonl` schema validation + redaction rules
- [x] Add golden tests with a tiny synthetic `chunks.jsonl` fixture.
- [ ] Add an integration test for `fileparse run --steps extract-text,chunk,triage` producing stable outputs.

## Rollout sequence (minimize risk)

- [x] **Report-only:** triage computes scores/routes but does not affect LLM selection; outputs metrics only.
- [x] **Soft gating:** filter only the lowest-score tail (`skip`) with conservative thresholds.
- [x] **Full routing:** enable `llm_small/llm_large` splits + budget caps; add caching.
- [x] **Optional ML routing:** train and evaluate a routing classifier; keep deterministic fallback.

## Known edge cases (to design for)

- Non-English text: keyword packs and rules may underperform; avoid over-skipping.
- OCR noise: strong pattern hits (dates/phones/addresses) must override low quality.
- Tables/forms: often high value but low “narrative”; add table-ish heuristics.
- Very short chunks: prefer per-file caps + allowlist rather than naive skipping.
