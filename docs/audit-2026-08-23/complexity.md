# Аудит надмірної складності: невикористана універсальність

**Дата:** 2026-08-23. **Гілка:** `anya-pipeline`. **Мандат:** знайти механізми,
оголошені як загальні, у яких немає ні другого користувача, ні читача.

Мірка не «складно на смак», а конкретна: **невикористана універсальність**.
Кожен рядок — з доказом (рядок коду, вивід grep-а або цифра прогону).

Вердикти:
- **зайве** — прибрати: механізм нічого не тримає й ні про що не попереджає;
- **навмисне оголошення** — лишити: є коментар або запис у
  `docs/known-weak-spots.md`, який робить оголошення документацією межі;
- **працює** — має реального читача/другого користувача, знахідкою не є.

---

## 0. Що вже ловить сам валідатор

`build_resources` (`pipeline/run.py:227`) кладе попередження в `res["warnings"]`.
Прогін на робочому `config.yaml` дає **9 попереджень**:

| # | попередження | ключ |
|---|---|---|
| 1-2 | `ключ схеми 'out_of_scope' не читається кодом` (обидві схеми) | `out_of_scope` |
| 3-6 | `ключ 'registry' не читається кодом` (`destination_org`, `authorizing_commander` ×2, `unit_to_report`, `stops`) | `registry` |
| 7 | `поле 'stops': ключ 'multiple' не читається кодом` | `multiple` |
| 8-9 | `довідник 'leave_type' завантажено, але на нього не посилається жодне поле жодної схеми` | `leave_type.yaml` |

Перелік ведеться явно — `identification.py:203-205`:

```python
DECLARED_BUT_UNREAD_FIELD_KEYS = {"multiple", "registry"}
DECLARED_BUT_UNREAD_SCHEMA_KEYS = {"out_of_scope"}
```

плюс `UNREAD_KEY_CONSEQUENCE` (`identification.py:212-218`), який каже, ЩО
станеться зі значенням. Тобто всі чотири вже заміряні рев'ю 22.08 випадки —
**навмисні оголошення**, і механізм їх видимості працює.

Далі — те, чого валідатор НЕ бачить.

---

## 1. Ключі схем і довідників без читача (те, чого валідатор НЕ бачить)

Метод: усі листові ключі обох `pipeline/schemas/*.yaml` і всіх
`pipeline/dictionaries/*.yaml` витягнуто програмно (`yaml.safe_load` + обхід
дерева), і для кожного зроблено grep рядкового літерала `"ключ"` / `'ключ'` по
всіх `.py` репозиторію. Нижче — лише ті, де читач ОДИН або його немає.

| механізм | де оголошений | скільком читачам служить | доказ | вердикт |
|---|---|---|---|---|
| `unmatched_term_policy: queue_for_review` | `dictionaries/leave_type.yaml:78`, `dictionaries/military_rank.yaml:121` | **0** | `grep -rn unmatched_term_policy` дає РІВНО два рядки — самі оголошення. Жодного `.py`. Політика «незнайдений термін → у чергу на перегляд» реалізована в коді іншим шляхом і ключ не читає | **зайве** |
| `value_free_text: true` | `leave_ticket.yaml:114`, `deployment_certificate.yaml:139` | 1 (валідатор) | `identification.py:360` — `and not field.get("value_free_text")` глушить попередження «основний факт без `category:`» | **працює** (це прапорець-квитанція, він і має бути читаний лише валідатором) |
| `identification.description` | обидві схеми | 1 | `identification.py` (одне входження) | **працює** |
| `registry`, `multiple`, `out_of_scope` | обидві схеми | 0 читачів, але 1 попередження | `DECLARED_BUT_UNREAD_*` + `UNREAD_KEY_CONSEQUENCE` (`identification.py:203-218`), вивід у розд. 0 | **навмисне оголошення** |
| `leave_type.yaml` (весь довідник) | `dictionaries/leave_type.yaml` | 0 полів схем | попередження №9 у розд. 0 + `unused_dictionaries()` | **навмисне оголошення** (коментар `run.py:270-276` прямо каже «лишається на майбутнє») |
| `fact_type_registry`: коди `attendance`, `equipment_status`, `rank` | `dictionaries/fact_type_registry.yaml` | 0 полів схем | програмна звірка: 20 кодів у реєстрі, 16 із них досяжні через `fact_type:`/`dimension:`; `unrecognized` дає `FREEFORM_SCHEMA` (`run.py:99`); ці три — ніхто | **навмисне оголошення** (реєстр — контракт із БД-споживачем, коди для майбутніх бланків; але це НЕ видно в жодному попередженні — див. розд. 5 «що варто прибрати») |

### Ключі, що мають рівно одного читача й це нормально

`label_preceded_by` (`extract.py:1917`), `multiple_matches` (`extract.py:1471`),
`empty_pattern` (`extract.py:1718`), `not_issued_sentinel`
(`normalize.py:766`), `value_starts_after` (`extract.py`), `strip_prefix`,
`min_value`/`max_value`, `consistency.not_before` (`build_record.py:212`) —
у кожного один читач, але це не «універсальність без користувача», а
однозначний контракт «ключ → рядок коду». Усі мають тест
(`eval/tests/test_new_fields_2026_08_23.py`, `test_review_fixes.py`).
**Працює.**

---

## 2. Ручки конфігу й схеми, яких ніхто не виставляє

### 2.1 `pipeline/config.py` DEFAULTS проти `config*.yaml`

Метод: усі 31 листовий параметр `DEFAULTS` звірено з grep-ом читачів у `.py` і
з наявністю ключа в `config.yaml`, `config.example.yaml`,
`demos/upload_app/config-app.yaml`, `demos/upload_app/config-gpu.yaml`.

| параметр | читачі | хто виставляє | вердикт |
|---|---|---|---|
| `llm.chat_format` (`"gemma"`) | 1 (`run.py:317`) | **жоден** | **зайве**: коментар у `config.py` сам доводить, що правильне значення тут РІВНО ОДНЕ («явний `"gemma"` тут КРАЩИЙ за автовизначення», llama-cpp на jinja-шляху губить BOS). Ручка з одним правильним значенням — не гнучкість |
| `llm.verbose` (`False`) | 1 (`run.py:323`) | **жоден** | **зайве** (наскрізь у `llama_cpp`; налагоджувальний прапорець без жодного споживача) |
| `llm.n_ctx` (`4096`) | 1 прод (`run.py:314`) + тест | **жоден** | **навмисне оголошення**: контекст моделі — параметр ваг, зміна ваг вимагає зміни числа |
| `llm.n_threads` (`None`) | `run.py:316` → `client.py:57` | **жоден** | **навмисне оголошення** (наскрізь у llama.cpp; `None` = «не чіпати») |
| `llm.subject_kind` (`False`) | 4 (`run.py:476`, `subject_kind.py`) | **жоден** | **навмисне оголошення**: `run.py:460-473` — 14 рядків коментаря, чому це окремий прапорець із власним заміром. Наслідок, який варто бачити: **рівень 3 визначення виду суб'єкта (`subject_kind.py:192-226`) не виконується в жодній робочій конфігурації** |
| `paths.schemas_dir`, `paths.dictionaries_dir`, `paths.llm_context` | по 1-3 | **жоден** із 4 конфігів | **працює** (шляхова індирекція, яку розгортає `_PATH_KEYS`; альтернатива — константа в коді) |

### 2.2 Родина «дефолт у коді + перевизначення в YAML схеми»

Це найбільше скупчення невикористаної універсальності в пайплайні. Патерн
оголошено п'ять разів, а СПРАЦЮВАВ (тобто якийсь YAML його виставив) — один раз.

| ручка | оголошення | хто читає | хто ВИСТАВЛЯЄ | доказ | вердикт |
|---|---|---|---|---|---|
| `identification.min_score` | `DEFAULT_MIN_SCORE = 5` (`identification.py:43`) | `identification.py` | `leave_ticket.yaml`, `deployment_certificate.yaml` | обидві схеми мають `min_score: 5` | **працює** |
| `identification.llm_floor` | `DEFAULT_LLM_FLOOR` (`identification.py:54`) | `identification.py:832-835` | **жодна схема** | `grep -rn llm_floor` по `*.yaml` — 0 збігів | **зайве** |
| `identification.min_blank_coverage` | `DEFAULT_MIN_BLANK_COVERAGE = 0.5` (`identification.py:97`), `MIN_BLANK_COVERAGE_KEY` (:100) | `identification.py:123` | **жодна схема** (є тест: `test_foreign_edition.py:119`) | `min_blank_coverage` у YAML згадується лише в КОМЕНТАРІ `leave_ticket.yaml:19` («можна перевизначити нижче ключем») — і ніде нижче не перевизначено | **навмисне оголошення** (поріг заміряний, A-12 у `known-weak-spots.md`) |
| `extraction_limits` (3 підключі) | `EXTRACTION_LIMITS_KEY` + `KNOWN_EXTRACTION_LIMITS` + `schema_extraction_limit()` (`extract.py:169-179`) + гілка валідатора (`identification.py:279-289`) | 3 місця в `extract.py` | **жодна схема** (є тест: `test_review_fixes.py:373`) | ключ `extraction_limits` відсутній у витягу ключів обох схем | **зайве** (передчасне узагальнення: механізм + закритий enum + гілка валідатора існують під ТРЕТЮ форму бланка, якої немає) |
| `placeholder_tokens` / `placeholder_tokens_except` | `normalize.py:41-42` | `normalize.py` | **жодна схема** (є тести: `test_regressions.py:883,892`) | `grep -rn placeholder_tokens *.yaml` — 0 збігів | **зайве** (дві ручки на те саме: повна заміна + виняток із дефолту; коментар `normalize.py:62` сам пише «два переліки того самого рано чи пізно...») |
| `domains.<домен>.min_score` | `DEFAULT_DOMAIN_MIN_SCORE = 2` (`classify.py:30`) | `classify.py:219` | **жоден домен** у `domain_keyphrases.yaml` | ключ `min_score` відсутній у витягу ключів `domain_keyphrases.yaml` | **зайве** |

**Разом по родині:** з шести оголошень патерну реально працює одне
(`min_score` схеми), одне обґрунтоване заміром (`min_blank_coverage`), решта
чотири — ручки, у яких немає ні другого користувача, ні читача поза тестом,
що доводить саме їхнє існування.

---

## 3. Параметри функцій із єдиним значенням у всіх викликах

Метод: AST-обхід усіх `.py` репозиторію — для кожної функції з
`extract.py` / `identification.py` / `build_record.py` / `blank_form.py` /
`normalize.py` / `subject_kind.py` / `classify.py` / `schema_grammar.py`
зібрано всі виклики й порівняно фактичні значення кожного kwarg.

Результат: **жодного справжнього випадку «аргумент, який усі виклики
передають однаково».** Усі знайдені «одне значення» — це проброс змінної
з тим самим іменем (`oversized_chars=oversized_chars`,
`printed=printed`, `boundaries=compile_value_boundaries(field)`), тобто
значення обчислюється в місці виклику й не є константою.

Що варто відзначити окремо:

| функція | виклики | зауваження | вердикт |
|---|---|---|---|
| `_fix_date_part(value, allow_month_name)` | 4 виклики, `allow_month_name=True` у всіх, що його передають | `normalize.py:200,226` — прапорець із єдиним значенням у прод-коді | **зайве** (один із двох реальних кандидатів цього класу) |
| `process_target(..., force_template, reprocess)` | 1 прод-виклик (`run_pipeline.py:87`) | обидва прокидаються з CLI (`--template`, `--reprocess`) — тобто другий користувач є, це людина | **працює** |
| `extract_field_regex(field_def, text)` | 1 прод-виклик (`extract.py:1957`) + 10 у тестах | публічна функція шару, тести — другий користувач | **працює** |
| `resegment_by_blank`, `resegment_text`, `ground_llm_value`, `is_placeholder`, `proves_empty` | 1-2 прод-виклики, 4-25 усього | різниця йде з тестів; це шар, а не універсальність «на всяк випадок» | **працює** |

Функцій із НУЛЕМ викликів у пайплайні немає: три кандидати
(`extract_year_suffix`, `_days_span_inclusive`, `fix_declared_numeric`)
викликаються через таблиці-реєстри (`DERIVE_FUNCS`, `CONSISTENCY_RULES`) або
через псевдонім (`fix = fix_declared_numeric if was_expanded else ...`,
`extract.py:1445`) — AST-обхід їх не бачить, це хибний позитив методу, не
знахідка.

---

## 4. Абстракція на одного споживача

| механізм | де оголошений | скільком служить | доказ | вердикт |
|---|---|---|---|---|
| `DERIVE_FUNCS` — таблиця дериваторів | `build_record.py:136-138` | **1 запис, 1 поле** | у таблиці рівно `{"extract_year_suffix": extract_year_suffix}`; єдиний споживач — `leave_ticket.yaml:144` (`derive: extract_year_suffix` на `leave_type_and_destination`) | **навмисне оголошення** (закритий перелік, як `NAME_PART_ROLES`; коментар `normalize.py:457` називає його «власником знання про рік») — але ціна: механізм `extraction: derived_from` + `derived_from:` + `derive:` = 3 ключі схеми, гілка валідатора й окрема гілка `build_record` заради одного суфікса року |
| `KNOWN_LINK_TYPES = {"supersedes"}` | `identification.py:219` | 1 тип | єдиний тип; але це закритий enum валідатора, який ловить одруківку в `link_type:` | **навмисне оголошення** |
| `GROUP_MODES = ("rank_and_name_tokenized",)` | `identification.py:155` | 1 режим | єдиний груповий режим витягу | **навмисне оголошення** (перелік потрібен, щоб `EXTRACTION_REQUIRED_KEYS` не вимагав лейбла на кожному полі групи) |
| `EXTRACTION_LIMITS_KEY` + `KNOWN_EXTRACTION_LIMITS` + `schema_extraction_limit()` + гілка валідатора | `extract.py:169-179`, `identification.py:279-289` | **0 схем**, 1 тест | див. розд. 2.2 | **зайве** |
| `placeholder_tokens` / `placeholder_tokens_except` | `normalize.py:41-42` | **0 схем**, 2 тести | див. розд. 2.2. Дві ручки на ту саму задачу, самі себе й називають підозрілими: «два переліки того самого рано чи пізно...» (`normalize.py:62`) | **зайве** |
| Рівень 3 визначення виду суб'єкта (LLM + закритий enum) | `subject_kind.py:192-226` + `run.py:460-478` | 0 прогонів (прапорець `llm.subject_kind` не виставлений НІ В ОДНОМУ з 4 `config*.yaml`) | `grep -rn 'subject_kind' config*.yaml demos/upload_app/config-*.yaml` — 0 збігів | **навмисне оголошення** (14 рядків коментаря `run.py:461-473` — точка розширення з явно відкладеним заміром) |

---

## 5. Два механізми про те саме

### 5.1 `PROVEN_EMPTY_REASONS` проти схемного `empty_pattern` — **справді різні**

Обидва дають ОДНАКОВИЙ провенанс `confirmed_empty_slot:<сигнал>`
(`extract.py:1630`), тобто зовні виглядають як дублювання. Заміряно на 107
документах (leave docx+pdf, deployment docx+pdf, demo-story `story` + `bulk`),
з розбивкою «поле → сигнал»:

| поле | сигнал | разів |
|---|---|---|
| `co_travelers` | `blank_value` | 101 |
| `destination_points` | `blank_value` | 2 |
| `destination_place` | `printed_form_text` | 4 |
| `rank` / `surname` / `given_name` / `patronymic` | `printed_hint` | по 4 |
| `duration_days` | `empty_pattern` | 6 |
| `leave_start_date` | `empty_pattern` | 6 |
| `leave_end_date_planned` | `empty_pattern` | 6 |
| `actual_return_date` | `empty_pattern` | 36 |

**Множини полів РОЗДІЛЬНІ: жодне поле за весь прогін не отримало обидва
сигнали.** `empty_pattern` спрацьовує рівно на тих чотирьох полях, які його
оголошують (`leave_ticket.yaml`: `duration_days`, `leave_start_date`,
`leave_end_date_planned`, `actual_return_date`) — і саме на цій групі
«днів + 3 дати», яку називає коментар `extract.py:1692-1700`.

Причина розділення видна з патернів: порожній слот дати виглядає як
`з «___» ____ 20__ р.` — це не placeholder у розумінні `_BLANK_FILL_RE`
(`blank_value`) і не літеральний рядок порожнього бланка
(`printed_form_text`), тому П5-А його не доводить за побудовою.

**Висновок: не дублювання.** Два різні КЛАСИ доказу (результат
`validate_*_value` проти оголошеного схемою скелета порожнечі) на роздільних
множинах полів, зведені до одного провенансу навмисно — щоб рев'юер бачив
один статус, а після двокрапки — яким саме сигналом він доведений. Прибирати
жоден не можна: 17 із 45 порожніх слотів у прогоні доводить ЛИШЕ
`empty_pattern`, і без нього ці поля пішли б у LLM-фолбек.

### 5.2 Справжнє дублювання, знайдене поруч

`placeholder_tokens` (повна ЗАМІНА дефолтного переліку) і
`placeholder_tokens_except` (виняток ІЗ дефолтного переліку) — дві ручки, що
розв'язують ту саму задачу «схема хоче інший перелік placeholder-токенів».
Жодна не виставлена жодною схемою (розд. 2.2). Коментар
`normalize.py:62` сам це визнає. **Дублювання, обидві сторони мертві.**

### 5.3 `not_issued_sentinel` + `normalization: null_if_not_issued`

Два ключі на одну поведінку: `normalization: null_if_not_issued` каже «зробити
null», `not_issued_sentinel` каже «за яким рядком». Читаються одним рядком
(`normalize.py:766`), обидва потрібні, бо один — вибір режиму, другий — дані.
**Не дублювання, працює.**

---

## 6. Мертві гілки провенансу/причин: цифри прогонів

### Умови замірів

**Важливо про стан дерева.** На початку сесії дерево було чисте на `38c8d84`.
Під час аудиту в репозиторії паралельно працював інший агент: HEAD переїхав на
`c1598e2`, з'явився `stash@{0}: agent4-wip`, і в робочому дереві виникла
незакомічена зміна `pipeline/extraction/extract.py`:
`MAX_PDF_WRAP_LOOKBACK_LINES = 3 → 2`. Перший прогін застав `3`, другий — `2`,
і різниця видна: **на `3` весь корпус `leave-pdf` давав `needs_review` 16/16,
на `2` — `confirmed` 15/16.** Усі цифри нижче взято ОДНИМ прогоном на
зафіксованому стані `HEAD=c1598e2` + цей один незакомічений рядок.

Прогони (усі `llm.enabled=false`, `ocr.engine=none`, `read_only`,
`reprocess=True`):

| корпус | файлів | статуси |
|---|---|---|
| `leave/synthetic-2026-05/docx` | 16 | confirmed 15, needs_review 1 |
| `leave/synthetic-2026-05/pdf` | 16 | confirmed 15, needs_review 1 |
| `deployment/synthetic-2026-05/docx` | 14 | confirmed 13, needs_review 1 |
| `deployment/synthetic-2026-05/pdf` | 14 | confirmed 13, needs_review 1 |
| `demo-story/story` | 17 | confirmed 16, needs_review 1 |
| `normative` (нормативний корпус через `process_file`) | 42 | confirmed 41, unresolved 1 |
| `holdout` | 4 | unresolved 4 |
| **разом** | **123** | **1453 записи провенансу полів** |

Плюс цільовий прогін на 60 документах, підібраних САМЕ під підозрілі гілки
(чужа редакція бланка, два порожні шаблони, holdout із `force_template`,
`demo-story/bulk` 20, `story-pdf` 5, `live` 2).

### Що трапилось хоч раз

| значення | де | разів (123-док. прогін) |
|---|---|---|
| `matched` | `extract.py:877,951,1061,…` | 1148 |
| `no_value` | `extract.py:1038,1083,1481` | 151 |
| `deferred` | `build_record.py:317` | 112 |
| `confirmed_empty_slot:blank_value` | `extract.py:1041,1098` | 23 |
| `confirmed_empty_slot:empty_pattern` | `extract.py:1718` | 17 |
| `confirmed_empty_slot:printed_hint` | `extract.py` | 4 |
| `confirmed_empty_slot:printed_form_text` | `extract.py:1049,1100` | 1 |
| `derived` | `build_record.py` (DERIVE_FUNCS) | 42 |
| morphology `already_nominative` | `normalize.py:672,684` | 216 |
| morphology `untagged_name` | `normalize.py:662` | 9 |
| morphology `skipped` | `normalize.py:629,634` | 6 |

Лише в цільовому прогоні (тобто гілка ЖИВА, але поза основними корпусами):

| значення | на чому спрацювало | разів |
|---|---|---|
| `no_label` | чужа редакція (9), holdout з `force_template` (5×4) | 29 |
| `unverified_foreign_edition` | чужа редакція (4), holdout (1×4) | 8 |
| `printed_form_text` (як самостійний провенанс, не доказ порожнечі) | порожні шаблони обох бланків | 3 |
| `rank_not_in_dictionary` | holdout (звання, якого немає в `military_rank.yaml`) | 4 |
| morphology `untagged_oblique` | holdout | 1 |

### Мертві: НІ РАЗУ на 183 документах

| значення | де оголошено | тестів | вердикт |
|---|---|---|---|
| `ambiguous_label` | `extract.py:860,872,941,944` (4 місця) | 2 | **навмисне оголошення** (захисна гілка: лейбл трапився двічі й межа неоднозначна) |
| `denylisted` | `extract.py:876,948` | 1 | **навмисне оголошення** |
| `oversized_block_suspect` | `extract.py:878,950` | 1 | **навмисне оголошення** |
| `printed_label_in_value` | `extract.py:1054` | 4 | **навмисне оголошення** |
| `type_mismatch` | `extract.py:1059` | 3 | **навмисне оголошення** |
| `ambiguous_multiple_matches` | `extract.py:1406,1459-1481` | **0** | **зайве-під-питанням**: механізм C-03 (рев'ю 22.08) не спрацював ані на 123, ані на 60 цільових документах, і в `eval/tests/` його не перевіряє жоден тест. Гілка існує заради заміряного випадку, який у корпус не потрапив |
| `name_tail_unparsed` | `extract.py:1726` | **0** | те саме: R-B1-02 («ЛЕМЕШКО Соломія Мустафа кизи») — 0 прогонів, 0 тестів |
| `positional_name_no_uppercase` | `extract.py:1746` | **0** | те саме (A-05). Заміряний випадок — holdout-форма, а holdout без LLM ідентифікується як чужа редакція й падає в `no_label` ДО позиційного розбору: 4/4 holdout-документи дали `no_label` + `unverified_foreign_edition`, жоден — `positional_name_no_uppercase` |
| `unknown_consistency_rule` | `build_record.py:196,208` | **0** | **навмисне оголошення** (подвійний запобіжник: валідатор схем відкидає невідоме правило раніше — сам коментар це й каже) |
| `consistency_error` | `build_record.py:216,226` | 1 | **працює як запобіжник**: механізм ВИКОНУЄТСЯ (заміряно інструментуванням: 47 документів → 73 виклики `check_consistency`, з них `days_span_inclusive` 47 і `not_before` 26, **усі 73 без проблеми**). Гілка помилки мертва бо дані узгоджені, а не бо код мертвий |
| `unverifiable_dependency` | `build_record.py:211` | 1 | те саме |
| morphology `not_a_name` | `normalize.py:658` | 1 | **навмисне оголошення** |
| morphology `inflect_failed` | `normalize.py:690` | **0** | **зайве-під-питанням**: 0 прогонів, 0 тестів, а `UNRELIABLE_MORPHOLOGY` на неї спирається |
| morphology `ambiguous_case` | `normalize.py:686` | 1 | **навмисне оголошення** |
| morphology `no_morphology` | `normalize.py:638` | 1 | **навмисне оголошення** (pymorphy не встановлено) |
| morphology `normalized` | `normalize.py:691` | 1 | **знахідка**: `normalized` означає «морфологію ЗАСТОСОВАНО і вона щось змінила». За 183 документи — **нуль разів**; усі 216 випадків — `already_nominative`. Тобто відмінювання ПІБ на наявних корпусах не змінює жодного значення, і вся вага pymorphy3 у прогоні витрачається на підтвердження «і так називний» |
| `ungrounded_llm_value`, `llm`, `llm_split_vote` | `extract.py:1554`, `extract.py:2090` | — | **не знахідка**: недосяжні за побудовою в `--no-llm`, тобто прогін про них нічого не каже |
| ident-причини `no_template_match`, `ambiguous`, `multiple_templates_matched` | `identification.py:879,881,888` | — | **зайве-під-питанням**. Заміряно інструментуванням `identify_template` на **269 документах** (усі корпуси + `bulk`, `story-pdf`, `live`, «вільні» файли): трапляються РІВНО чотири результати — `None`+`leave_ticket` 127, `None`+`deployment_certificate` 91, `procedural_document:normative` 41, `below_llm_floor` 5. `no_template_match`, `ambiguous` і `multiple_templates_matched` — **нуль разів**. Наслідок: `FREEFORM_ELIGIBLE_REASONS = {"no_template_match", "below_llm_floor"}` (`run.py:79`) містить один мертвий елемент із двох |

---

## 7. Що варто прибрати — за співвідношенням «скільки коду піде / який ризик»

Впорядковано від найдешевшого й найбезпечнішого до найдорожчого.

| # | що прибрати | скільки коду | ризик | чому це не смак |
|---|---|---|---|---|
| 1 | `unmatched_term_policy: queue_for_review` з обох довідників | 2 рядки YAML (+3 рядки коментаря) | **нуль**: ключ не читає ЖОДЕН рядок коду | `grep -rn unmatched_term_policy` по всьому репозиторію дає рівно ці два рядки. Небезпека не в самому ключі, а в тому, що він читається як ГАРАНТІЯ: наступний автор довідника вважатиме, що політикою черги керує саме він. Точно той самий клас, що `normalization:` на ПІБ (A-04) |
| 2 | `llm.chat_format`, `llm.verbose` із DEFAULTS | 2 рядки `config.py` + 2 рядки `run.py` (значення в літерал `LlamaClient`) | **нуль**: жоден `config*.yaml` їх не виставляє; `client.py` уже має ті самі дефолти | коментар у `config.py` сам доводить, що правильне значення `chat_format` РІВНО ОДНЕ. Ручка з одним правильним значенням — це не гнучкість |
| 3 | `identification.llm_floor` як ключ схеми | `DEFAULT_LLM_FLOOR` лишається константою, зникають 4 рядки читання ключа (`identification.py:832-835`) | **низький**: жодна схема ключа не має | 0 схем-установників, 0 тестів на override |
| 4 | `domains.<домен>.min_score` як ключ домену | `DEFAULT_DOMAIN_MIN_SCORE` лишається, зникає читання ключа (`classify.py:219-221`) | **низький** | 0 доменів-установників у `domain_keyphrases.yaml` |
| 5 | `placeholder_tokens` + `placeholder_tokens_except` | 2 ключі, ~20 рядків `normalize.py`, 2 тести | **низький-середній**: 0 схем-установників, але 2 тести доводять роботу механізму | справжнє дублювання (розд. 5.2), обидві сторони без користувача. Якщо лишати — лишати ОДНУ |
| 6 | `extraction_limits` (ключ + `KNOWN_EXTRACTION_LIMITS` + `schema_extraction_limit` + гілка валідатора) | ~15 рядків `extract.py`, ~10 рядків `identification.py`, 3 місця виклику, 1 тест | **середній**: конструкція існує саме під ТРЕТЮ форму бланка. Прибирати — означає повернути 3 числа в код | найдорожчий приклад передчасного узагальнення: механізм + закритий enum + перевірка валідатора обслуговують 0 схем |
| 7 | `no_template_match` із `FREEFORM_ELIGIBLE_REASONS` | 1 елемент множини (`run.py:79`) | **низький**, але вимагає рішення | на 269 документах ця причина не трапилась НІ РАЗУ (розд. 6). Або елемент мертвий, або корпус не покриває клас — і саме це варто записати замість тихої множини з двох |
| 8 | `ambiguous_multiple_matches`, `name_tail_unparsed`, `positional_name_no_uppercase`, morphology `inflect_failed` | 4 гілки, ~30 рядків | **НЕ прибирати** | у всіх чотирьох 0 прогонів **І 0 тестів** — але кожна відповідає ЗАМІРЯНОМУ випадку рев'ю (C-03, R-B1-02, A-05). Правильна дія — не видалення, а **тест або документ у корпусі**, інакше гілка непідтверджена й у неї немає жодного доказу, що вона працює |

### Три речі, які не «прибрати», а зробити видимими

1. **Рівень 3 визначення виду суб'єкта** (`subject_kind.py:192-226`) не
   виконується ні в одній із чотирьох конфігурацій. Це навмисно й описано, але
   ніде в прогоні про це не сказано — на відміну від мертвого довідника
   `leave_type`, який дає попередження. Той самий клас, різна видимість.
2. **Коди `attendance`, `equipment_status`, `rank`** у
   `fact_type_registry.yaml` не досяжні жодним полем жодної схеми (розд. 1).
   Мертвий ДОВІДНИК видимий у попередженні, мертвий КОД реєстру — ні.
3. **`normalized` як стан морфології не трапився ні разу на 183 документах**
   (розд. 6): усі 216 випадків — `already_nominative`. Це не зайвий код, це
   заміряний факт, що відмінювання на наявних корпусах не змінює жодного
   значення — і його варто записати в `known-weak-spots.md`, бо він означає,
   що вся ця гілка перевіряється лише синтетикою.

---

## Підсумок

- **зайве (прибрати):** 8 механізмів —
  `unmatched_term_policy`, `llm.chat_format`, `llm.verbose`,
  `identification.llm_floor`, `domains.min_score`, `extraction_limits`,
  `placeholder_tokens` + `placeholder_tokens_except`, аргумент
  `allow_month_name` у `_fix_date_part`.
- **навмисне оголошення (лишити):** 14 —
  `registry`, `multiple`, `out_of_scope`, `leave_type.yaml`, коди реєстру
  фактів, `min_blank_coverage`, `llm.n_ctx`, `llm.n_threads`,
  `llm.subject_kind` (+ рівень 3), `DERIVE_FUNCS`, `KNOWN_LINK_TYPES`,
  `GROUP_MODES`, `unknown_consistency_rule`, захисні гілки
  `ambiguous_label`/`denylisted`/`oversized_block_suspect`/`printed_label_in_value`/`type_mismatch`.
- **зайве-під-питанням (не видаляти, а покрити):** 5 гілок без прогону Й без
  тесту — `ambiguous_multiple_matches`, `name_tail_unparsed`,
  `positional_name_no_uppercase`, morphology `inflect_failed`,
  `no_template_match`.
- **дублювання:** знайдено одне справжнє (`placeholder_tokens` /
  `placeholder_tokens_except`); підозра на `PROVEN_EMPTY_REASONS` проти
  `empty_pattern` **не підтвердилась** — множини полів роздільні (розд. 5.1).

Нічого в коді цим аудитом не змінено.
