# CODEX.md — How Codex Should Operate In This Repo

## Operating mode
- Be explicit and minimal. Prefer the smallest change that solves the task.
- Do not refactor broadly unless asked. No architecture redesigns unless requested.

## Before editing
- Briefly state your plan (2–6 bullets).
- If the task is ambiguous, ask ONE question OR make the safest assumption and state it clearly.

## While editing
- Keep changes scoped.
- Avoid introducing new dependencies unless necessary; explain why if you add any.
- Prefer clear code over clever code. Avoid hidden "magic" behavior.

## Safety rules (must follow)
- Do not create/switch branches unless the user explicitly asks.
- Do not commit unless the user explicitly asks.
- Do not push unless the user explicitly asks.
- Do not create duplicate *_v2 / *_new files.

## Output format after code changes
End your response with:
1) Summary
2) Files changed
3) Commands to run
4) Commands to test
5) Any notes/risks
