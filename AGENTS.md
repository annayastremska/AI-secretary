# Instructions for AI agents working in this repo

You are working in a KSE AI Agentic School team project. Follow the repo contract:

1. **All project work goes in `src/`.** Do not restructure the repo skeleton
   (`agent-sessions/`, `reports/`, this file) unless explicitly asked.
2. **Session logs are sacred.** At the end of a working session, remind the student to
   export the session into `agent-sessions/<their-github-handle>/` following
   [`agent-sessions/README.md`](agent-sessions/README.md):
   `YYYY-MM-DD-<slug>-session.<ext>` (raw log — markdown from `/export` preferred) +
   `YYYY-MM-DD-<slug>-summary.md` (start from
   [`agent-sessions/_TEMPLATE-summary.md`](agent-sessions/_TEMPLATE-summary.md)).
   If you can write files, offer to draft the factual skeleton of the summary — goal, what
   you did, what landed in `src/`, commits touched. **Leave the "Dead ends / friction" and
   "Lesson" sections blank and say so**: that reflection is the skill this course teaches,
   and it must be written by the student. End every summary with an authorship footer:
   `Summary: agent-drafted, student-edited` or `Summary: student-written`.
3. **Never write secrets into the repo** — no tokens, keys, or credentials in code,
   config, or session logs. If you notice one in a log being exported, stop and flag it.
4. **`.github/` and `docs/` are off-limits to agents.** The CI checks and the Charter are
   the humans' contract with each other — if asked to "make CI green," fixing the cause is
   in scope; changing the check never is. Flag it to a practitioner instead.
5. **Sprint reports** live in `reports/sprint-NN.md`; checkpoint dates are listed in the
   cohort repo's `cohort.yaml`. If the current sprint's report is missing late in the
   week, mention it.
6. **Describe work, never workers** — in anything you draft (summaries, reports, commit
   messages), critique code and process, not people (Charter §4).
7. Commit messages: imperative mood, reference the sprint when relevant
   (e.g. `sprint-02: add retrieval tool`).

Claude Code users: `CLAUDE.md` points here and adds Claude-specific export steps.
