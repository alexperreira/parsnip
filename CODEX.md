# CODEX.md — How Codex Should Operate In This Repo

## Operating mode
- Be explicit and minimal. Prefer the smallest change that solves the task.
- Do not refactor broadly unless asked. No architecture redesigns unless requested.

## Before editing (mandatory)
For every request, first:
1) State a 2–6 bullet plan.
2) List the files you expect to touch.
3) If anything is ambiguous, ask ONE clarifying question OR state your safest assumption.

## While editing
- Keep changes scoped.
- Avoid introducing new dependencies unless necessary; explain why if you add any.
- Prefer clear code over clever code. Avoid hidden "magic" behavior.
- Favor determinism over heuristics.

## Git safety (non-negotiable)
- Do not create or switch branches unless explicitly asked.
- Do not commit unless explicitly asked.
- Do not push unless explicitly asked.
- Never amend commits without explicit approval.
- Never run destructive git operations (e.g., git reset --hard, git restore) unless explicitly instructed.

## Output format after code changes
End your response with:
1) Summary
2) Files changed
3) Commands to run
4) Commands to test
5) Any notes/risks
