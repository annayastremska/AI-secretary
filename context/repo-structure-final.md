# Фінальний розклад структури репозиторію

Зведено 14.08.2026 після погодження правок: повний розклад, за яким можна
переносити без питань у чаті. Попередній документ із заперечень видалено —
все чинне з нього тут.

Шість наших правок, які команда прийняла, і два додані правила — уже враховані
в дереві й таблицях нижче, окремо їх перечитувати не потрібно.

## Фінальне дерево

```
docflow-expertise/
├── README.md · CLAUDE.md
├── requirements.txt · requirements-optional.txt
├── config.example.yaml · run_pipeline.py
│
├── pipeline/                    АНЯ · розбір документа
│   ├── ingestion/ ocr/ classification/ extraction/
│   ├── normalization/ llm/ storage/
│   ├── llm_context/             системний промпт моделі
│   ├── schemas/                 опис бланків
│   ├── dictionaries/            довідники значень
│   ├── notebooks/               експерименти з моделями й OCR
│   ├── build_record.py · identification.py · run.py
│   ├── subject_kind.py · config.py
│   └── README.md
│
├── eval/                        АНЯ · вимірювальний прилад (НЕ в pipeline/)
│   ├── evaluate.py
│   ├── field-mapping.yaml
│   ├── tests/                   test_regressions.py · test_evaluator.py
│   └── README.md
│
├── db/                          АНДРІЙ
├── answer/                      КОЛЯ + ДЕНИС
│   ├── chat/
│   └── knowledge-base/
│
├── data/                        СПІЛЬНЕ
│   ├── generator/
│   ├── eval/
│   │   ├── synthetic-2026-05/   еталон: документ + правильна відповідь
│   │   └── samples/             окремі зразки, у т.ч. публічні норм-акти
│   └── inbox/                   .gitkeep, вміст у .gitignore
│
├── docs/                        СПІЛЬНЕ
│   ├── known-weak-spots.md      живий список, у корені навмисно
│   ├── open-questions.md        живий список, у корені навмисно
│   ├── collaboration-rules.md
│   ├── spec/ research/ architecture/ contracts/ client/
│   └── scripts/download_model.py -> лишається в pipeline/scripts/
│
└── demos/                       СПІЛЬНЕ
```

Дві відмінності від початкової пропозиції, які варто побачити одразу:
**`eval/` виведено з `pipeline/`** (причина нижче, у розділі про прилад) і **два живі
списки лежать у корені `docs/`**, а не в підпапках.

## Розклад нашої частини

### Код: `pipeline/`

| Зараз | Куди |
|---|---|
| `pipeline/**` (усі 8 підмодулів + `llm_context/`) | без змін |
| `schemas/` (2 файли) | `pipeline/schemas/` |
| `dictionaries/` (4 файли) | `pipeline/dictionaries/` |
| `notebooks/` (5 ipynb) | `pipeline/notebooks/` |
| `scripts/download_model.py` | `pipeline/scripts/` |
| `run_pipeline.py` · `config.example.yaml` · `requirements*.txt` | корінь |

`pipeline_trial_leave_colab.ipynb` зі списку **видалено** 11.08 — викликав
старий API і вводив в оману.

### Прилад: `eval/` — окремо від `pipeline/`

| Зараз | Куди |
|---|---|
| `scripts/evaluate.py` | `eval/evaluate.py` |
| `data/eval/field-mapping.yaml` | `eval/field-mapping.yaml` |
| `tests/test_regressions.py` · `tests/test_evaluator.py` | `eval/tests/` |

Прилад мусить лишатися незалежним від того, що міряє. Приклад із цього тижня:
в оцінювачі свідомо тримається ВЛАСНА копія таблиці гомогліфів, а не імпорт з
`pipeline.normalization` — інакше прилад і вимірюваний код помилялися б
однаково, і порівняння сходилося б за побудовою. Якщо файл лежить у
`pipeline/`, хтось цілком розумно захоче цей «дубляж» прибрати.

### Дані: `data/`

| Зараз | Куди |
|---|---|
| `data/eval/synthetic-2026-05/` (32 файли) | без змін |
| `data/samples/leave/` · `deployment/` (127 файлів) | `data/eval/samples/` |
| `data/samples/normative/` (12 файлів) | `data/eval/samples/normative/` |
| `data/samples/equipment/` · `staffing/` (6 файлів) | `data/eval/samples/` |
| `data/inbox/.gitkeep` | без змін |
| `data/eval/reports/` | не їде — прибрано з git 14.08.2026, див. нижче |

**Норм-акти не дублюємо.** Файл лежить один раз у `data/eval/samples/normative/`
(ми використовуємо його як тестовий вхід), а `answer/knowledge-base/README.md`
на нього посилається. Копія в двох місцях розійдеться.

### Написане: `context/` → `docs/`

Дев'ятнадцять файлів — стільки ж, скільки в початковій пропозиції, але склад
інший: додались раунди досліджень 12-13.08, а три документи-листування
(дві відповіді команді й приклад виводу) видалено 14.08 — чинне з них зведено
в `db-handoff-notes.md`, розд. 9.

| Файл | Куди |
|---|---|
| `known-weak-spots.md` | `docs/` — живий список |
| `open-questions.md` | `docs/` — живий список |
| `collaboration-rules.md` | `docs/` |
| `ТЗ_AI-секретар.docx` | `docs/spec/` |
| `project-overview.md` · `project-expectations.md` | `docs/spec/` |
| `scale-and-users.md` · `security-constraints.md` | `docs/spec/` |
| `data-and-sources.md` | `docs/spec/` — це обмеження проєкту, не опис вмісту папки |
| `architecture-proposal.md` | `docs/architecture/` |
| `extraction-pipeline-prototype.md` | `docs/architecture/` |
| `normative-docs-subsystem.md` | `docs/architecture/` — це проєкт підсистеми, не нормативний акт |
| `agent-pipeline.md` | `docs/architecture/` |
| `research-insights.md` | `docs/research/` |
| `research-round-2026-08-11.md` | `docs/research/2026-08-11_pipeline-audit/` |
| `research-round-2026-08-12.md` | `docs/research/2026-08-12_ocr-geometry-speed/` |
| `research-round-2026-08-13.md` | `docs/research/2026-08-13_weak-spots-clusters/` |
| `db-handoff-notes.md` | `docs/contracts/2026-08-11_database-handoff.md` |
| `repo-structure-final.md` | `docs/contracts/` |

## Що прибираємо з git

`data/eval/reports/` — дев'ять файлів відстежувались, хоч папка вже була в
`.gitignore`. Це виходи прогонів, які перезаписуються щоразу; самі цифри й що
вони означають живуть у `docs/`. Прибрано через `git rm --cached` 14.08.2026
(файли лишились на диску).

Тоді ж у `.gitignore` додано класи, яких не було: архіви (`*.zip`, `*.rar`,
`*.7z`, `*.tar*`), дампи бази (`*.sql`, `*.dump`), секрети (`.env`, `*.pem`,
`*.key`), логи в корені, `.pytest_cache/`, `.vscode/`, `.idea/` і `~$*`
(файли-замки Word). Архіви — навмисно цілим класом: саме архівом найпростіше
занести пачку реальних сканів одним `git add`. Це те саме, що правило 1
нижче, тільки виконане тим, що вже є в репо.

## Два правила, які додаємо

**1. Заборона реальних документів — перевіркою, а не рядком у README.**
У проєкті це найдорожче обмеження (`security-constraints.md`: питання
кримінальної відповідальності). Правило в README тримається на увазі людини о
другій ночі перед демо. Pre-commit або CI-крок, що відхиляє коміт із файлами
під `data/inbox/`, `data/output/`, `.env`, `*.zip` і дампами бази — десять
рядків, і обмеження діє саме тоді, коли на нього перестають дивитись.

**2. Еталонний набір не редагується, щоб цифри зійшлися.**
`data/eval/synthetic-2026-05/` — це відповіді. Коли результат не збігається,
змінюють правило порівняння або код, але не відповіді.

Живий приклад: 13.08 виявилось, що оцінювач карав пайплайн за чесний `null` і
зарахував би вигадане значення — еталон на документах із порожніми полями
очікував значення зі сценарію, а не з паперу. Виправили правило порівняння
(читаємо блок «надруковано» самого еталона), відповідей не чіпали — і те саме
виправлення одразу виявило справжній баг у пайплайні, який м'яке порівняння
приховувало. Легким шляхом загубили б і те, і те.

## Порядок

1. **Ми** робимо перенос трьома комітами у своїй гілці:
   - `pipeline/` (схеми, довідники, ноутбуки, `scripts/`) **разом** із правкою
     `config.example.yaml` — інакше між двома комітами репо зламане;
   - `eval/` (прилад + тести + мапінг) із правкою шляхів у ньому;
   - `context/` → `docs/` за таблицею вище, з проходом по посиланнях.
2. **Денис** заводить каркас у `main` і заливає своє.
3. **Ми** зливаємо гілку — шляхи вже не конфліктують.

Правило на весь репо: **хто переносить файл, той і править посилання в ньому.**
Наші документи посилаються один на одного за назвою; без цього проходу частина
посилань стане мертвою, і ніхто цього не помітить.

## Одне питання по власності

`docs/contracts/` позначені як «спільне, власника немає». Стик пайплайна з
базою — найризикованіша ділянка проєкту: за останні дні там знайшлося кілька
місць, де дані тихо губились або дублювались. Пропонуємо в
`docs/contracts/README.md` вказувати **двох названих власників** на кожну
домовленість, по одному з кожного боку стику. «Спільне» тут означає «нічиє».
