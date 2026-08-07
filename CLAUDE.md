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

---

# ai-secretary

Капстоун №21, KSE Agentic AI Summer School 2026. Що це за проєкт і як він
працює — `README.md`. Тут тільки те, що агент мусить знати перед тим,
як щось писати.

## Правило даних — жорстке

**У git не їде жодна бойова, тактична або персональна інформація.**

Джерело документів — військова частина, а репозиторій побачить університет.

- Реальні документи — тільки локально, у `data-private/` (вона в `.gitignore`)
- Синтетичні зразки — у `eval/synthetic/`
- `knowledge-base/` — тільки **публічні** нормативні акти. Внутрішні документи
  частини туди не кладемо, навіть якщо вони описують ту саму процедуру
- Сумнів, чи можна файл у git → не кладемо

Побачив у діффі реальний документ — зупиняєшся і кажеш, не комітиш.

## Мова

Код, назви папок і файлів — англійською. Коментарі, документація, комміти — українською.

## Структура

Папка верхнього рівня = ділянка з одним власником. У корінь код не кладемо.

| Папка | Власник | Що там |
|---|---|---|
| `pipeline/` | Аня | OCR → класифікація → витяг полів → нормалізація |
| `db/` | Андрій | MinIO (об'єкти) + Postgres (факти) |
| `knowledge-base/` | Коля | публічні норм-акти |
| `eval/` | — | синтетичний корпус + еталонні відповіді |
| `docs/` | Денис | мапа доріг, схеми |

У кожній папці свій `README.md` — читати перед роботою в ній.

## Два правила продукту, які легко зламати кодом

- **Чернетка ≠ факт.** Запис із хоч одним непевним полем у підрахунки не входить,
  доки людина не підтвердить. Це має бути видно і в базі, і у відповіді
- **Відмова краща за вигадку.** Немає даних — система каже «не знайшла».
  Цифра або цитата без джерела гірша за відсутність відповіді
