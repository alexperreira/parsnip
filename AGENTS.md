# AGENTS.md — Repository Rules (File Parser)

This repo is being developed with Codex CLI. These rules exist to prevent costly mistakes
(scope creep, unsafe data handling, messy diffs, and unstable behavior).

## Non-Negotiables

### Git safety
- DO NOT create/switch branches unless the user explicitly asks.
- DO NOT commit unless the user explicitly asks.
- DO NOT push unless the user explicitly asks.
- If the working tree is dirty and a task requires a clean state, STOP and tell the user.

### Scope & change control
- One task per prompt. If a request includes multiple tasks, split work and do the first task.
- Prefer small diffs. If a change touches >3 files or >200 LOC, explain why before proceeding.
- Do not introduce major new subsystems (queues, databases, cloud services, Rust rewrites, etc.)
  unless explicitly requested.

### No “V2 sprawl”
- Do not create duplicate files like `*_v2`, `*_new`, `*_old`, or “copy of …”.
- Prefer editing existing code and extracting small helpers/modules when needed.

### Correctness & determinism
- Prefer deterministic behavior over heuristics and "magic" guessing.
- If unsure about behavior, ask or leave a TODO — do not silently guess.

## Security & Privacy Defaults

- Never transmit file contents to third parties by default.
- Never log raw file contents by default.
- Treat all input (filenames, paths, metadata, parsed text) as untrusted.
- Avoid shelling out with unsanitized paths; avoid command injection risk.
- Logs should be redacted/summarized (hash or truncate sensitive strings).

## Performance Defaults

- Stream large files when possible; avoid loading huge blobs into memory.
- Use concurrency with explicit limits; never spawn unbounded workers.
- Optimize after measurement. If performance work is proposed, include a way to profile/measure.

## File Parser Domain Requirements

### Pipeline (default)
- Two-phase approach:
  1) enumerate + lightweight metadata
  2) deep parsing only when needed (by type, keyword hits, size limits, user rules, etc.)

### Parser contract
- File-type handlers must follow a consistent interface (metadata, text extraction, entities).
- Fail-soft: one bad file must not crash the whole run; errors are collected and summarized.

### Observability (without data leakage)
- Track: files scanned, parsed by type, failures by type, time per stage.
- Error samples must be redacted.

## Expected “handoff” after changes

Whenever code is changed, the assistant must end with:
- Summary of what changed
- Files changed
- How to run
- How to test (at least one concrete command)
- Known limitations / edge cases

## Additional collaboration and git rules

- Delete unused or obsolete files when your changes make them irrelevant (refactors, feature removals, etc.), and revert files only when the change is yours or explicitly requested.
- Before attempting to delete a file to resolve a local type/lint failure, stop and ask the user.
- Never edit `.env` or any environment variable files—only the user may change them.
- Coordinate with other agents before removing their in-progress edits—don't revert or delete work you didn't author unless everyone agrees.
- Moving/renaming and restoring files is allowed.
- ABSOLUTELY NEVER run destructive git operations (e.g., `git reset --hard`, `rm`, `git checkout`/`git restore` to an older commit) unless the user gives an explicit, written instruction in this conversation. Treat these commands as catastrophic; if you are even slightly unsure, stop and ask before touching them. (When working within Cursor or Codex Web, these git limitations do not apply; use the tooling's capabilities as needed.)
- Never use `git restore` (or similar commands) to revert files you didn't author—coordinate with other agents instead so their in-progress work stays intact.
- Always double-check git status before any commit.
- Keep commits atomic: commit only the files you touched and list each path explicitly. For tracked files run `git commit -m "<scoped message>" -- path/to/file1 path/to/file2`. For brand-new files, use the one-liner `git restore --staged :/ && git add "path/to/file1" "path/to/file2" && git commit -m "<scoped message>" -- path/to/file1 path/to/file2`.
- Quote any git paths containing brackets or parentheses when staging or committing so the shell does not treat them as globs or subshells.
- When running git rebase, avoid opening editors—export `GIT_EDITOR=:` and `GIT_SEQUENCE_EDITOR=:` (or pass `--no-edit`) so the default messages are used automatically.
- Never amend commits unless you have explicit written approval in the task thread.
