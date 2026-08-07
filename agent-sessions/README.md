# agent-sessions/ — the bones

One folder per student, named by GitHub handle. Every working session with an AI agent
leaves two files:

```
agent-sessions/
└── <your-github-handle>/
    ├── 2026-07-14-first-scraper-session.jsonl   # raw exported log (or .md from /export)
    └── 2026-07-14-first-scraper-summary.md      # ★ required sibling — human-readable
```

Naming: `YYYY-MM-DD-<short-slug>-session.<ext>` + `YYYY-MM-DD-<short-slug>-summary.md`.
Several sessions a day are fine — vary the slug. See
[`_example-student/`](_example-student/) for a filled-in example — that folder and
[`_TEMPLATE-summary.md`](_TEMPLATE-summary.md) are the only items checks skip; every other
folder here follows the convention. (The example omits its raw-log sibling only because it
is illustrative; your real folders always contain both files.)

## How to export

- **Claude Code**: prefer `/export <path>` — the markdown it writes is the default raw
  artifact here. Raw JSONL from `~/.claude/projects/` is version-unstable and captures
  terminal output and file contents beyond this repo — the classic credential-leak path.
  If you commit JSONL, scan it extra carefully.
- **Other agent CLIs**: commit whatever transcript the tool produces (JSONL/JSON/MD) —
  the format matters less than the habit. Raw formats are often version-unstable, which is
  exactly why the summary sibling is required.

## Why we ask for this

Practitioners read these to see *how* you drive an agent — where it saved you a day, where
you fought it for an hour, what prompt finally worked. That's the skill this course teaches,
and it's invisible in the final diff. The cohort digest at each checkpoint is built from
your summaries and commit metadata — never your raw logs by default
([`../docs/CHARTER.md`](../docs/CHARTER.md) §1); good summaries get your struggles noticed
and unblocked.

This is the lab-notebook tradition — 350 years old, from the Royal Society to patent
notebooks — applied to agent work. Notebooks are graded for presence and honesty, never
polish: a session that went nowhere, honestly recorded, is credited — see
[`../docs/CHARTER.md`](../docs/CHARTER.md) for the full assessment contract.

Write summaries in English or Ukrainian — whichever lets you think; practitioners read
both (Charter §2).

## Privacy & secrets

- These logs are visible to your team and to practitioners — same as the rest of the repo.
  Don't paste anything personal into an agent session you wouldn't put in the repo.
- **Scrub credentials before committing.** Terminal output captured in logs loves to leak
  tokens and connection strings. `bones-check` fails on anything credential-shaped, but
  it's a net, not a guarantee — look before you commit. Also keep raw exports lean —
  anything over ~10MB gets flagged and over ~50MB fails CI (prefer `/export` markdown over
  raw JSONL, or split the session).

## Your rights

- You may **redact personal content from a raw log before commit** — just keep the summary
  truthful.
- Your logs are used only for the purposes listed in [the Charter](../docs/CHARTER.md) —
  anything else needs your opt-in.
- At cohort end you leave with your repo and your logs.
- Redaction or deletion requests go to the data steward named in the Charter.
