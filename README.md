# AI secretary — шар відповіді над документообігом

Капстоун-проєкт №21, KSE Agentic AI Summer School 2026.

## Що це

Замовник — військова частина з великим обсягом паперового документообігу.
Інформація зафіксована в рапортах, наказах, довідках. Щоб отримати зведену
картину, зараз треба обійти кількох людей і порахувати руками — це години або дні.

Система читає документи, дістає з них дані у структурований вигляд і відповідає
на зведені запитання за секунди.

Ми не робимо документообіг — він у замовника вже є. Ми робимо **шар відповіді**
над документами, які вже ходять.

## Дві дороги відповіді

Спільна база документів, два різні механізми. Крок маршрутизації вирішує, якою
дорогою іде конкретне питання.

| | Дорога А — підрахунок | Дорога Б — нормативка |
|---|---|---|
| Питання | «Скільки людей зараз у відпустці?» | «Яка процедура подання заяви на відпустку?» |
| Механізм | факти з документів → цифра | питання → цитата з норм-акта |
| Відповідь | цифра + склад відповіді | цитата + посилання на джерело |

**Склад відповіді** — чотири обов'язкові елементи біля цифри: дата зрізу,
знаменник, кількість непідтверджених, посилання на документ-джерело. Приберемо
одне — людина піде дзвонити, і система не потрібна.

Якщо система не знайшла даних, вона каже «не знайшла». Відмова краща за
вигадану цифру.

## Що входить до другого демо

Один тип документа — **рапорт на відпустку** — проходить весь ланцюг наскрізь:
вхід → розбір → база → відповідь у вікні чату. Решта типів документів — після.

Один робочий контур замість трьох недороблених.

## Де що лежить

| Папка | Що там |
|---|---|
| `pipeline/` | розбір документа: OCR → класифікація → витяг полів → нормалізація |
| `db/` | сховище: об'єкти (MinIO) і факти (Postgres) |
| `knowledge-base/` | джерела для дороги Б — **тільки публічні нормативні акти** |
| `eval/` | синтетичний корпус і еталонні відповіді для перевірки якості |
| `docs/map/` | мапа двох доріг — жива схема архітектури |

## Як запустити

Поки нічим — код у роботі. Інструкція запуску з'явиться тут, коли перша дорога
замкнеться наскрізь.

## Дані

**У цьому репозиторії немає жодного реального документа замовника.**
Тільки синтетика й шаблони. Це жорстке правило проєкту, не рекомендація —
див. `CLAUDE.md`.

## Команда

| Хто | Ділянка |
|---|---|
| Денис | керування проєктом |
| Аня | розбір документів, пайплайн |
| Андрій | база даних |
| Коля | контент, нормативні джерела |

У кожної ділянки один власник — одне ім'я, яке в будь-який момент відповідає,
у якому це стані і коли запрацює.


---

# Team project — KSE AI Agentic School

This repo is your team's home for the course. It was stamped from
`student-project-template`, and the skeleton is part of the course contract — the
`bones-check` workflow keeps it honest.

Welcome — you were selected from 103 applicants, and you belong here. Struggle is expected
content in this repo: the most useful logs are the ones where it went sideways.
Practitioners are here to unblock you — ask early, in Slack.

## The contract

```
src/                 your project — code, notebooks, docs, whatever the work is
agent-sessions/      ★ required — exported AI-agent session logs, one folder per student
reports/             your sprint report, one file per sprint
README.md            keep the project brief + roster below up to date
```

1. **Work in `src/`.** Any language, any stack — the project is yours.
2. **Export your agent sessions.** After every working session with an AI agent
   (Claude Code or any other CLI), the log lands in `agent-sessions/<your-github-handle>/`
   as a raw log **plus a short summary**. How and why: [`agent-sessions/README.md`](agent-sessions/README.md).
   This is not surveillance theater — practitioners read these to teach you better, and
   *how you drive the agent* is part of what this course grades —
   [docs/CHARTER.md](docs/CHARTER.md) states exactly what is graded, what is never graded,
   and what practitioners owe you in return.
3. **Report each sprint.** Copy [`reports/_TEMPLATE-sprint-report.md`](reports/_TEMPLATE-sprint-report.md)
   to `reports/sprint-NN.md` before the Saturday checkpoint.
4. **Never commit secrets.** Not in code, and not in session logs — scrub tokens before
   exporting. `bones-check` fails the build on anything that looks like a credential.

## Who sees what

Your team has `push` here. Practitioners (`@KSE-AI-Agentic-School/practitioners`) can see
and maintain every course repo — including this one. Other teams cannot see your repo at
all. Each Saturday the cohort repo pins the exact commit of your `main` that gets reviewed —
push your sprint's work before the checkpoint.

Two honest details: other teams do see your commit subjects each Saturday (the checkpoint
PR in the cohort repo carries the pin log; your code and logs stay private to your team +
practitioners). And at cohort end you keep this repo and your logs — the org keeps only the
pinned snapshots. Lost power before a checkpoint? Flag it — a practitioner re-pins your
team without penalty (see [the Charter](docs/CHARTER.md)).

## Project brief

> Replace this section: what you're building, for whom, and what "done" means this sprint.

## Roster

| student | GitHub | focus |
|---|---|---|
| … | @… | … |
