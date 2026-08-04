# Claude Code — project instructions

Follow [`AGENTS.md`](AGENTS.md) — it is the contract for every agent in this repo.

Claude-specific export mechanics (see `agent-sessions/README.md` for the full convention):

- `/export <path>` writes the current conversation — export to
  `agent-sessions/<github-handle>/YYYY-MM-DD-<slug>-session.md`. Prefer this markdown over
  copying raw JSONL from `~/.claude/projects/` — JSONL is version-unstable and captures
  terminal output and file contents beyond this repo.
- The raw JSONL format is internal to Claude Code and changes between versions — that's why
  a human-readable `…-summary.md` sibling is required next to every raw log.
- Offer to draft the factual skeleton of the summary at session end: goal, what you did,
  what landed in `src/`, next steps. Leave **"Dead ends / friction"** and **"Lesson"**
  blank — that reflection must be written by the student, and say so. End with an
  authorship footer: `Summary: agent-drafted, student-edited` or `Summary: student-written`.
  Keep it under a page.
- Before the file is committed, scan it for anything credential-shaped and tell the student
  to scrub it. `bones-check` will fail the push otherwise.
