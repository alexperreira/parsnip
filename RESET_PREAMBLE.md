# Reset Preamble — File Parser (Codex)

Use this file to orient new Codex sessions quickly. Keep edits small and scoped.

## Repo purpose
- Python-first file parsing pipeline with a deterministic two-phase approach.
- Phase 0 builds a JSONL manifest of PDFs (including PDFs inside zip files).
- Phase 1 classifies PDFs as text/scanned/mixed/unknown using lightweight signals.
- Phase 2 runs OCR for scanned/mixed PDFs via external tools when available.

## Key commands
```bash
PYTHONPATH=src python -m file_parser.manifest_builder --input /path/to/input --output output/manifest.jsonl
PYTHONPATH=src python -m file_parser.phase1_detect --input /path/to/input --manifest output/manifest.jsonl --output output/phase1.jsonl
PYTHONPATH=src python -m file_parser.phase1_report --input output/phase1.jsonl
PYTHONPATH=src python -m file_parser.phase2_ocr --input /path/to/input --phase1 output/phase1.jsonl --output output/phase2_ocr.jsonl
```

## Dependencies and environment
- Phase 0 and report: stdlib only.
- Phase 1: requires `pypdf` (see `requirements.txt`).
- Phase 2: requires `pypdf`, plus `tesseract` and `pdftoppm` on PATH.
- A venv is convenient but not required if your active Python has `pypdf`.

## Repo rules (non-negotiables)
- Do not create/switch branches, commit, or push unless explicitly asked.
- One task per prompt. Prefer small diffs; avoid broad refactors.
- No duplicate files like `*_v2`, `*_new`, `*_old`.
- Treat filenames/paths/metadata/parsed text as untrusted input.
- Do not log raw file contents; redact or summarize.
- Fail-soft: one bad file must not crash the run.
- Keep output deterministic; avoid heuristics without clear rules.

## Editing workflow expectations
- Before editing: state a short plan (2–6 bullets).
- Keep changes scoped and avoid new dependencies unless necessary.
- If behavior is unclear, ask or leave a TODO rather than guessing.

## Testing
- pypdf and other tools will often be missing from environment.
- start python venv and install requirements to run tests.

## Handoff after changes
- Summary of changes.
- Files changed.
- How to run.
- How to test (at least one command).
- Tests passed.
- Known limitations or edge cases.
