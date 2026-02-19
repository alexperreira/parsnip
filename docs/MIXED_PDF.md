# L2 Plan: Smarter Mixed-PDF Handling

This plan is derived from `docs/TODO_LONG_TERM.md` (L2):

- For `mixed` PDFs, extract embedded text where available.
- OCR only image-heavy pages.
- Add per-page quality scoring.

## Scope

In scope:
- Phase 2 and Phase 3 logic for `classification == "mixed"`.
- Add deterministic per-page OCR routing.
- Add additive per-page quality fields.
- Add validation metrics and tests.

Out of scope:
- Reworking Phase 1 global classification model.
- Any schema-breaking changes to downstream phases.
- Distributed processing or major performance subsystem changes.

## Current Behavior (Baseline)

- Phase 1 classifies whole PDFs as `text|scanned|mixed|unknown`.
- Phase 2 OCRs all pages for `scanned|mixed|unknown`.
- Phase 3 uses:
  - embedded PDF text for `text`
  - OCR output for `scanned|mixed|unknown`
- Phase 3 has document-level `quality_score`, but no explicit per-page quality score.

## Design Decisions (Lock Before Coding)

- Determinism: routing is rule-based only (no model-based guessing).
- Backward compatibility: Phase 3 page schema remains additive (no field removals/renames).
- Fail-soft: per-page failures are captured on page records; one bad page/file does not abort the run.
- Mixed routing policy:
  - `mixed` page uses embedded text when sufficient text is present.
  - OCR only runs for image-heavy / low-text pages.
- Threshold source: reuse existing Phase 1 signal defaults to avoid drift:
  - `TEXT_PAGE_MIN_CHARS = 50`
  - `LOW_TEXT_MAX_CHARS = 10`

## Proposed Data Contract Changes (Additive)

Phase 2 page record additions (`src/file_parser/phase2_ocr.py` output):
- `ocr_decision`: `"ocr" | "skip_pdf_text" | "skip_no_image" | "error"`
- `ocr_reason`: short deterministic reason string.
- `signal_text_chars`: integer.
- `signal_has_image`: boolean.

Phase 3 page record additions (`src/text_extraction/phase3_extract_text.py` output):
- `quality_score_page`: float in `[0,1]`.
- `quality_flags`: list of strings (for example: `empty_text`, `low_text`, `ocr_error`, `missing_source`).
- `text_char_count`: integer.

Notes:
- Keep existing fields (`source`, `text`, `confidence`, `review_required`, `review_reason`) unchanged.
- Keep document-level `quality_score`; recompute from page-level scores (mean).

## Codex-Ready TODO Checklist

## 0) Baseline and Safety
- [x] Run `git status --short` and confirm no unexpected unrelated edits.
- [x] Run baseline tests:
  - `pytest -q tests/test_phase2_skeleton.py tests/test_phase3_resume.py tests/test_phase7_validate.py`

## 1) Add Shared Per-Page Signal Helper
Target file:
- `src/file_parser/pdf_page_signals.py` (new)

Tasks:
- [x] Implement helper to inspect each page with `pypdf` and return:
  - `page_index`, extracted text, `text_char_count`, `has_image`.
- [x] Implement deterministic decision helper:
  - `should_ocr_mixed_page(text_char_count, has_image, text_page_min_chars, low_text_max_chars)`.
- [x] Keep helper side-effect free and unit-testable.

## 2) Phase 2: OCR Only Needed Mixed Pages
Target file:
- `src/file_parser/phase2_ocr.py`

Tasks:
- [x] Add CLI options:
  - `--mixed-ocr-mode {all,image-heavy}` (default: `image-heavy`)
  - `--text-page-min-chars` (default `50`)
  - `--low-text-max-chars` (default `10`)
- [x] For `classification == "mixed"` and mode `image-heavy`, build per-page routing plan using shared helper.
- [x] Render/OCR only planned pages.
- [x] Preserve page index alignment: output includes one page entry per page index even when OCR is skipped.
- [x] Populate additive decision/signal fields on each page record.
- [x] Keep existing resume behavior unchanged.

## 3) Phase 3: Hybrid Merge for Mixed PDFs
Target file:
- `src/text_extraction/phase3_extract_text.py`

Tasks:
- [x] For `mixed` docs, merge per-page sources using deterministic precedence:
  - 1) embedded PDF text when `text_char_count >= text_page_min_chars`
  - 2) OCR text when available
  - 3) empty text with `review_required=true` + reason
- [x] Add per-page quality scoring helper.
- [x] Add `quality_score_page`, `quality_flags`, `text_char_count` fields.
- [x] Recompute document-level `quality_score` as mean of page-level scores.
- [x] Ensure behavior stays deterministic and suffix/compression logic remains untouched.

## 4) Phase 7: Validation Metrics for Mixed Routing
Target file:
- `src/file_parser/phase7_validate.py`

Tasks:
- [x] Add counters for Phase 3 page sources:
  - `phase3_pages_pdf_text`, `phase3_pages_ocr`, `phase3_pages_low_quality`.
- [x] Add percentages:
  - `phase3_ocr_page_rate_pct`, `phase3_low_quality_page_rate_pct`.
- [x] Add warning when low-quality page rate crosses a fixed threshold (for example `>30%`).

## 5) Tests
Target files:
- `tests/test_phase2_skeleton.py`
- `tests/test_phase3_resume.py` (or new focused `tests/test_phase3_mixed_pdf.py`)
- `tests/test_phase7_validate.py`

Tasks:
- [x] Phase 2 mixed-page routing test:
  - mixed doc with one text-heavy page and one image-heavy page.
  - assert OCR executes only for image-heavy page.
- [x] Phase 3 merge test:
  - assert mixed doc output uses PDF text on text-heavy page and OCR on image-heavy page.
  - assert per-page quality fields exist and are bounded.
- [x] Phase 7 metric test:
  - assert new source/quality counters and rates are computed.

## 6) Documentation
Target files:
- `README.md`
- `docs/TODO_LONG_TERM.md` (optional status note)

Tasks:
- [x] Document new Phase 2 flags and mixed routing behavior.
- [x] Document new Phase 3 per-page quality fields.
- [x] Add a short operator note on expected speed/cost effect for mixed PDFs.

## Acceptance Criteria

- Mixed PDFs no longer OCR all pages by default.
- Text-bearing mixed pages use embedded text deterministically.
- Image-heavy/low-text mixed pages route to OCR deterministically.
- Phase 3 output includes per-page quality fields without breaking existing consumers.
- Phase 7 reports mixed-routing quality metrics.
- Targeted and regression tests pass.

## Suggested Execution Commands (When Implementing)

- `rg -n "mixed|quality_score|pages|ocr" src tests README.md -S`
- `pytest -q tests/test_phase2_skeleton.py`
- `pytest -q tests/test_phase3_resume.py`
- `pytest -q tests/test_phase7_validate.py`
- `pytest -q`
