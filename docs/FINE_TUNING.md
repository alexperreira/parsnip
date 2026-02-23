# Fine-Tuning Path (Route Classifier) — Implementation Plan

This document turns the “Fine-tuning path (later; optional)” section from `docs/COMPUTE_STRATEGY.md` into an executable plan with checkboxes.

Scope: **route classification** (predict `skip|llm_small|llm_large` from chunk text) with **raw text included** in the training dataset. This is intended to improve Compute Strategy routing while keeping deterministic budget caps and a deterministic fallback.

## Current repo state (as of this session)

Compute Strategy pipeline pieces implemented:

- Stage 1 + 2 signals: `src/triage/lightweight_signals.py` (stdlib signals + keyword packs + optional spaCy NER fail-soft).
- Stage 3 scoring/budgets: `src/triage/scoring.py` (`score_from_features`, `select_under_budgets`).
- Stage 4 triage + wiring: `src/triage/phase_t1_triage_chunks.py` + `fileparse triage` + `fileparse run --steps ...` wiring in `src/file_parser/cli.py`.
  - Emits `output/triage.jsonl`, `output/chunks.llm_small.jsonl`, `output/chunks.llm_large.jsonl`.
  - `fileparse run` currently feeds **only** `chunks.llm_small.jsonl` into LLM extractors (no second-pass over `chunks.llm_large.jsonl` yet).
- Stage 5 caching: `src/llm/cache.py` + `--cache/--cache-db/--cache-retry-errors` flags in:
  - `src/llm/extract_entities.py`
  - `src/llm/extract_events.py`
  - `src/llm/extract_conversations.py`
  - `src/llm/extract_identity_signals.py`
- Dataset builder for this fine-tuning path:
  - `src/triage/build_route_dataset.py` (CLI: `fileparse route-dataset`)
  - Writes `output/ml/route_dataset.jsonl` containing raw chunk `text`, `chunk_text_hash`, triage info, features (optional), LLM outcome summaries, and a heuristic `label_route` overrideable via a human `--labels` JSONL.

Tests exist for all of the above; `./.venv/bin/python -m pytest -q` passes.

## Definition of Done (fine-tuning path)

- You can generate a versioned training dataset (`route_dataset.jsonl`) reproducibly from pipeline outputs.
- You can train a route model artifact (baseline first), evaluate it (file-level split), and persist a report.
- `fileparse triage` can optionally load the model and choose routes from model probabilities with guardrails + Stage 3 budgets.
- Rollout is safe: report-only → shadow gating → full gating, with metrics to verify compute reduction without recall collapse.

## Data artifacts and conventions

- Chunk identity:
  - `chunk_id` is the primary join key today.
  - `chunk_text_hash` is computed from normalized text and used for label overrides and cache keys.
- Training rows:
  - Store raw `text` in `output/ml/` only (never commit to git).
  - Never log raw text in normal operation; log counts/hashes only.

## Plan (executable tasks)

### A) Dataset generation and hygiene

- [ ] Add `output/ml/` (and any dataset outputs) to `.gitignore` if not already ignored.
- [ ] Run triage to ensure `output/triage.jsonl` exists:
  - [ ] `PYTHONPATH=src python -m file_parser.cli triage --input output/text/chunks.jsonl --output-dir output`
- [ ] Build the route dataset (raw text included):
  - [ ] `PYTHONPATH=src python -m file_parser.cli route-dataset --chunks output/text/chunks.jsonl --triage output/triage.jsonl --output output/ml/route_dataset.jsonl --include-features`
- [ ] Decide and document the *official* route label set for v1 (recommended: `skip|llm_small|llm_large`) and keep it stable until you have enough labeled data for expansion.
- [ ] Add a small `output/ml/README.md` (not committed) describing what files are created and how to delete/regenerate them safely.

### B) Human labeling loop (optional but recommended)

Define a minimal human label file shape (JSONL):

- `chunk_id` (string)
- `chunk_text_hash` (string)
- `label_route` (string: `skip|llm_small|llm_large`)
- `label_source` (string, e.g. `human`)

Tasks:

- [ ] Create a `labels.jsonl` workflow (outside git) that samples “uncertain” chunks for review:
  - [ ] Add a small sampler script (or CLI option) that selects chunks near thresholds (e.g., score in `[0.05, 0.2]` and `[0.65, 0.85]`) and writes a review queue JSONL including `chunk_id`, `chunk_text_hash`, and a short, redacted preview (or no preview, depending on privacy needs).
- [ ] Rebuild dataset with `--labels labels.jsonl` and verify overrides apply:
  - [ ] `PYTHONPATH=src python -m file_parser.cli route-dataset --labels /path/to/labels.jsonl ...`

### C) Baseline trainer (first “fine-tune” iteration)

Implement a baseline route classifier that trains on raw text:

- Recommended baseline: TF-IDF (or hashing) vectorizer + multinomial logistic regression.
- Persist:
  - model artifact (e.g., `output/ml/route_model.pkl`)
  - `model_version.json` with dataset hash + feature config + class set
  - evaluation report `eval.json` (metrics + confusion matrix)

Tasks:

- [ ] Add `src/triage/train_route_model.py` with a CLI:
  - [ ] `--input output/ml/route_dataset.jsonl`
  - [ ] `--output-model output/ml/route_model.pkl`
  - [ ] `--output-report output/ml/route_eval.json`
  - [ ] `--split-by file_id` (required default)
  - [ ] `--random-seed` (deterministic)
- [ ] Add deterministic tests for:
  - [ ] stable train/test split by `file_id`
  - [ ] consistent label encoding
  - [ ] report schema presence
- [ ] Run: `./.venv/bin/python -m pytest -q`

### D) Evaluate against compute and recall proxies

Add explicit evaluation metrics geared toward compute strategy:

- Primary safety metric: recall of “send-to-LLM” (`llm_small|llm_large`) vs baseline heuristic.
- Cost metrics: `% routed to LLM`, estimated tokens routed, selected chunks per file distribution.
- Optional: compare predicted routes to “any_yield” outcomes and error rates by predicted route.

Tasks:

- [ ] Extend the trainer report to include:
  - [ ] confusion matrix for `skip/llm_small/llm_large`
  - [ ] recall/precision per class
  - [ ] “LLM workload” estimates per predicted class

### E) Integrate model inference into triage (guarded)

Extend `src/triage/phase_t1_triage_chunks.py`:

- Load model when a flag is present (e.g. `--ml-route-model output/ml/route_model.pkl`).
- For each chunk, compute model probabilities from raw chunk `text`.
- Decide route using policy:
  - Conservative defaults (example):
    - only label `skip` if `P(skip) >= 0.9`
    - label `llm_large` if `P(llm_large) >= 0.8` OR (low-quality + high heuristic score)
    - otherwise `llm_small`
  - Always apply Stage 3 budgets after routing.
- Emit in `triage.jsonl`:
  - [ ] predicted route
  - [ ] probability summary (no raw text)
  - [ ] which policy gates fired
- Keep deterministic fallback:
  - if model missing/unloadable, use `score_from_features` thresholding.

Tasks:

- [ ] Add `--ml-route-model` and `--ml-route-mode` flags to `fileparse triage` and `fileparse run`.
- [ ] Add “report-only” mode:
  - [ ] model predictions are computed and written, but chunk selection remains heuristic.
- [ ] Add tests for:
  - [ ] model-load failure is fail-soft (triage still runs)
  - [ ] report-only does not change outputs
  - [ ] budgets still apply

### F) Create a true `llm_large` label source (optional but unlocks better models)

Right now `llm_large` labels are heuristic-only because the pipeline does not run a second LLM pass.

Tasks:

- [ ] Add an optional two-pass evaluation mode (not default):
  - [ ] Run `llm_small` over `chunks.llm_small.jsonl`.
  - [ ] Then run `llm_large` over `chunks.llm_large.jsonl` (or only over “small failed / empty” candidates).
  - [ ] Store both outcomes, and update `route-dataset` builder to prefer empirical `llm_large` labels when available.

### G) Rollout plan (safe + measurable)

- [ ] Phase 1: report-only (model never affects selection)
- [ ] Phase 2: shadow gating (model can *add* chunks to LLM, not remove)
- [ ] Phase 3: full gating (model can exclude, with strict `P(skip)` threshold and budgets)
- [ ] Add metrics to `fileparse validate` (or a new summary) for:
  - [ ] chunks routed to LLM
  - [ ] estimated tokens sent
  - [ ] yield by predicted route
  - [ ] error rates by predicted route

## How to run (today)

- Build triage outputs:
  - `PYTHONPATH=src python -m file_parser.cli triage --input output/text/chunks.jsonl --output-dir output`
- Build route dataset:
  - `PYTHONPATH=src python -m file_parser.cli route-dataset --chunks output/text/chunks.jsonl --triage output/triage.jsonl --output output/ml/route_dataset.jsonl --include-features`

## How to test

- `./.venv/bin/python -m pytest -q`

## Known limitations (as of now)

- No model training/inference code is implemented yet; only dataset building exists.
- `llm_large` labels in the dataset are heuristic unless you add a second-pass LLM evaluation mode.
- `fileparse run` doesn’t pass LLM `--cache` flags yet; caching is enabled per-extractor CLI only.

