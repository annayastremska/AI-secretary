# A2 — незалежна рецензія реалізації пайплайна (14.08.2026)

Рецензент без контексту розмов команди. Читано: `README.md`, `CLAUDE.md`,
`pipeline/**`, `eval/**`, `docs/spec/**` (заголовково), `run_pipeline.py`,
`config.example.yaml`. НЕ читано (за мандатом): weak-spots, open-questions,
research, плани, contracts, git-історію.

## Що запущено й що вийшло

| Прогін | Результат |
|---|---|
| `pytest eval/tests -q` | 152 passed, 2.75s |
| eval leave/docx `--no-llm` | 176/176 (100%), статуси confirmed=15, needs_review=1 (LEAVE-011 empty_fields) |
| eval leave/pdf `--no-llm` | 176/176 (100%), ті самі статуси |
| eval deployment/docx `--no-llm` | 155/155 (100%), TRIP-011 swapped_dates → needs_review + date_range_error, 1/1 позначено |
| eval deployment/pdf `--no-llm` | 155/155 (100%), те саме |
| `run_pipeline --no-llm --dry-run` на порожньому `відпускний_шаблон.docx` | needs_review, критичні прогалини по всіх полях, друкований текст форми значенням НЕ віддається |
| те саме на «Статут гарнізонної...» і `інструкція_діловодство.docx` | unresolved, reason `procedural_document:normative` — нормативка не стає бланком |
| повторний прогін тієї самої папки | duplicate=16 — дедуплікація за хешем працює, `--reprocess` підказується |

Записи в `data/output/documents/leave/*.md` відкриті очима: YAML-шапка повна,
`field_provenance` на кожне поле (method/criticality/resolved/confidence,
morphology де доречно), `identification.blank_edition` з цифрами
(25/27, coverage 0.926, поріг 0.5), непідтверджені критичні поля справді
тягнуть `facts[0].confirmed: false` (LEAVE-011). Загальна якість зчеплення
«схема → двигун → запис» висока: усі оголошені механізми (`extraction:`
режими, `criticality:`, `consistency:`, `identification:` з анкорами/порогом/
llm_floor/min_blank_coverage, `placeholder_tokens*`, `value_starts_after`,
`strip_prefix`, `link_type`, `dimension:`) простежені до коду, який їх
реально читає; валідатор схем ловить одруківки в усіх цих ключах, КРІМ
одного (див. R-A2-05).

## Знахідки

```
id: R-A2-01
severity: critical
where: pipeline/run.py:498-556 (місцево 499, 512, 554-556) проти pipeline/build_record.py:564, 576
claim: Для класів template_by_llm / unknown_subject_kind / foreign_edition run.py гасить лише ЛОКАЛЬНУ змінну confirmed (статус документа), але не переписує facts[*]["confirmed"], тож у записі status=needs_review співіснує з confirmed:true у фактах.
evidence: Репро (LEAVE-001.docx, схема без subject_kind + домен без мапінгу): status=needs_review, review_reason=unknown_subject_kind, review_queue=unknown_type, АЛЕ facts[0].confirmed=True і всі 7 похідних фактів confirmed=True, unknown_critical_fields=[].
impact: Коментар у build_record.py:47-49 сам каже, що споживач фільтрує за facts.confirmed, а не за meta.status. Документ, чий шаблон вибрала модель (не анкори) або чий вид суб'єкта невідомий, потрапляє в підрахунки як підтверджений факт, хоча в черзі рев'ю він висить як unknown_type. Людина в черзі бачить документ «на розгляді», а цифра в відповіді вже його порахувала — рівно розрив «чернетка ≠ факт». Для foreign_edition цей самий розрив прикритий лише опосередковано (UNVERIFIED_METHOD робить непідтвердженими поля БЕЗ опори на бланк): схема, у якої всі критичні поля з label_before, у чужій редакції теж дасть confirmed:true при status=needs_review.
proposal: Після обчислення підсумкового confirmed у run.py явно проставити його в facts[0] і в усі похідні факти (record["facts"]), або передавати причини (template_by_llm/unknown_kind/foreign_edition) у build_record і рахувати confirmed там — одне джерело істини замість двох.
confidence: high
```

```
id: R-A2-02
severity: should-fix
where: eval/evaluate.py:430-584 (evaluate_record), eval/field-mapping.yaml
claim: Прилад міряє ЗНАЧЕННЯ полів і рівно одну поведінку статусу (суперечливий діапазон, range_checks); статуси confirmed/needs_review, review_queue, field_provenance для решти класів лише ДРУКУЮТЬСЯ, але не входять у ok/total.
evidence: evaluate_record: єдиний check із compare:"flag" — суперечність діапазону; `confirmed`, `status`, `unknown_critical_fields` ідуть у рядок звіту без оцінки. Пайплайн, що позначив би LEAVE-012 (unknown_person) чи будь-який morphology-непевний запис як confirmed, отримає ті самі 176/176.
impact: R-A2-01 приладом невидимий за побудовою: eval читає confirmed із facts[0], а асерта на «needs_review-документ не дає confirmed-факту» немає. Тобто пайплайн може «пройти прилад», порушуючи головне продуктове правило скрізь, крім swapped_dates. Регресію в гейті підтвердження ніхто не помітить, доки вона не зіпсує значення поля.
evidence2: заміряно: на 4 корпусах жоден check не стосується статусу, крім 2 range-conflict рядків.
proposal: Додати в еталон per-document очікуваний статус (або хоча б expected_confirmed: true/false для документів із вадами) і рахувати його як check — симетрично до вже зробленого для range_checks.
confidence: high
```

```
id: R-A2-03
severity: should-fix
where: eval/evaluate.py:342-427 (check_mapping)
claim: check_mapping перевіряє лише вже ОГОЛОШЕНІ ключі мапінгу; поля схеми, яких у мапінгу немає взагалі, не міряються і жодного попередження не породжують — клас «position_and_workplace прожив місяць невидимим» відтворюється для кожного нового поля.
evidence: Не міряються зовсім: leave — travel_document_number, leave_year, supersedes_document_number, supersession_note; deployment — basis_order_date, basis_order_number, supersedes_document_number, supersession_note. Коментар у deployment_certificate.yaml:209-212 сам фіксує, що basis_order_date уже давав 0/14 непомітно саме через відсутність у field-mapping.yaml — і його там досі немає.
impact: Поле з dimension: (тобто таке, що їде в БД окремим фактом — order_date, order_number, travel_document) може мовчки зламатись до 0/14, і всі агрегати лишаться 100%. Людина дізнається з бази, не з приладу.
proposal: У check_mapping додати зворотний прохід: кожне поле схеми з dimension: або db_target != additional_info, відсутнє в мапінгу, — попередження «поле НЕ міряється». Для basis_order_* дані на бланку є (ORDER_*? — перевірити ключі «надруковано») — додати рядки мапінгу.
confidence: high
```

```
id: R-A2-04
severity: should-fix
where: eval/evaluate.py:781-787; pipeline/build_record.py:482-495
claim: Механізм document_links (supersedes) реалізований і реально спрацьовує на LEAVE-014/016, TRIP-014, але прилад його не міряє взагалі, а фінальний друк стверджує протилежне: «пайплайн НЕ знає про скасування -- це очікувано».
evidence: evaluate.py:786-787 друкує це для кожної пари; водночас leave_ticket.yaml:260-284 і deployment_certificate.yaml:236-252 витягують позначку скасування, і в записі LEAVE-014 supersession_note = matched (у прогоні docx поле відсутнє серед прогалин).
impact: Регресія в supersedes-регексах (найкрихкіший режим за власною класифікацією коду) невидима: пари в звіті виглядають однаково до і після поломки, а стейл-коментар ще й переконує читача, що міряти нічого. Зв'язок, заради якого поле існує (закрити старий факт), губиться вдруге — цього разу в приладі.
proposal: Для документів категорії «пара» додати check: у чинного документа-заміни document_links непорожній і target_document_number збігається з номером скасованого з еталона; текст «пайплайн НЕ знає…» замінити на чесний «витягує позначку, зіставлення пари — на боці БД».
confidence: high
```

```
id: R-A2-05
severity: should-fix
where: pipeline/identification.py:validate_schema (немає перевірки), pipeline/normalization/normalize.py:660-702
claim: Значення ключа normalization: ніяк не валідується — одруківка тихо вимикає обробку, а обидві робочі схеми вже оголошують normalization: iso_date, якого КОД НЕ ЧИТАЄ ніде (date-поля диспетчеризуються за type).
evidence: normalize_field читає normalization лише для "nominative_case" і "null_if_not_issued"; "iso_date" (leave_ticket.yaml:165,176,229; deployment_certificate.yaml:158,168,292 та ін.) — мертвий рядок, що створює хибне враження в автора наступної схеми. Одруківка "null_if_not_issed" на travel_document_number: not_issued_sentinel виключається з placeholder-ів (field_placeholder_tokens), тому «не видавались» пройшло б у additional_info як РЕАЛЬНЕ значення поля, без confirmed_empty і без жодного маркера.
impact: Рівно той клас тихої помилки, який validate_schema ловить для extraction/type/consistency/part/db_target/link_type — але не для normalization. Підтверджено-порожнє поле перетворюється на текстове значення в БД.
proposal: Додати KNOWN_NORMALIZATIONS = {"nominative_case", "null_if_not_issued"} у валідатор (error на невідоме значення) і або прибрати iso_date зі схем, або зробити його легальним синонімом поведінки type: date.
confidence: high
```

```
id: R-A2-06
severity: should-fix
where: pipeline/run.py:716-734 (process_target, гілка except)
claim: Документ, що впав необробленою помилкою в пакетному прогоні, отримує запис ЛИШЕ в консолі: _persist не викликається (немає id/hash), файл не архівується (continue пропускає archive_input_file) і в індекс не потрапляє.
evidence: except-гілка робить results.append(blank_meta(...)); continue — жодного store.save, meta["id"]=None, review_queue="unknown_type" існує лише в пам'яті процесу.
impact: У запланованому запуску (консоль ніхто не читає) такий документ не існує ніде: не в data/output, не в data/failed, не в черзі рев'ю БД. Він лишається в папці-приймачі й падає знову на кожному прогоні — вічний невидимий цикл. Це суперечить власній гарантії docstring run.py «вихід існує завжди» в її сильному сенсі (вихід є, але слід — ні).
proposal: У except-гілці генерувати id, викликати _persist(meta, "", res) (unresolved-тека) і переносити файл у failed_dir тим самим archive_input_file; або щонайменше писати рядок у failed-лог поруч з індексом.
confidence: high
```

```
id: R-A2-07
severity: nit
where: pipeline/normalization/normalize.py:431 і pipeline/build_record.py:341
claim: Маркер «термін не розпізнано» — магічний український рядок, який продукується в одному модулі й порівнюється літералом в іншому, без спільної константи.
evidence: match_dictionary повертає "термін не розпізнано"; build_record: if normalized == "термін не розпізнано".
impact: Перейменування маркера в normalize.py тихо вимкнуло б конверсію в build_record, і рядок поїхав би в subject/fact_value буквальним текстом — рівно той сценарій, від якого код сам себе застерігає для UNVERIFIED_METHOD і PDF_TEXT_SOURCE (там імпорт констант зроблено саме з цієї причини).
proposal: Винести в константу UNRECOGNIZED_TERM у normalize.py та імпортувати в build_record (а краще — повертати сентинел-об'єкт/None+причину, щоб значення-рядок у принципі не міг збігтися).
confidence: high
```

```
id: R-A2-08
severity: nit
where: pipeline/normalization/normalize.py:429 (match_dictionary)
claim: Генеричний зіставлювач довідників хардкодить leave-специфічне зрізання суфікса «за NNNN рік» — воно застосовується до КОЖНОГО category-поля будь-якої схеми.
evidence: re.sub(r"\s*за\s*\d{4}\s*рік\s*[,.;]?\s*$", ...) усередині match_dictionary; походить від виду відпустки («щорічна основна відпустка за 2026 рік»), а military_rank чи майбутній довідник несправностей цього суфікса не мають.
impact: Малий, але це підгонка під один бланк у модулі, що оголошує себе доменно-нейтральним; майбутнє category-значення, що легітимно закінчується на «за 2026 рік», буде тихо обрізане перед пошуком аліаса.
proposal: Перенести правило в схему (ключ на кшталт strip_suffix_pattern на полі) або хоча б у довідник leave_type, а не в загальний код.
confidence: medium
```

```
id: R-A2-09
severity: nit
where: eval/evaluate.py:353-354 і 566-569
claim: Мапа «тип еталона → template» захардкоджена в коді оцінювача, ще й двома копіями.
evidence: tpl_of = {"відпускний квиток": "leave_ticket", "посвідчення про відрядження": "deployment_certificate"} у check_mapping; той самий literal-dict удруге в evaluate_record (template_ok).
impact: Новий шаблон вимагає правити evaluate.py (проти власного принципу «новий шаблон = новий YAML», прямо оголошеного в шапці field-mapping.yaml), а дві копії можуть розійтись — template_ok тоді бреше мовчки.
proposal: Перенести відповідність «тип → template» у field-mapping.yaml (по одному ключу на шаблон) і читати з одного місця.
confidence: high
```

```
id: R-A2-10
severity: nit
where: eval/evaluate.py:618-619 (RANK_ALIASES = res["dictionaries"]["military_rank"])
claim: Прилад бере аліаси звань із ТОГО САМОГО довідника, яким користується вимірюваний пайплайн — помилковий аліас у military_rank.yaml зробить обидва боки «правильними» одночасно.
evidence: Для гомогліфів той самий ризик усвідомлено знятий власною копією (_PRINTED_HOMOGLYPHS, коментар 165-168: «спільна функція означала б, що обидва боки помиляються однаково»); для аліасів звань виняток не обґрунтований.
impact: Аліас «капітан → major», дописаний у довідник помилково, дасть неправильний код у БД і 100% у приладі.
proposal: Або тримати в eval власний мінімальний список «надруковано → код» для звань набору, або хоч зафіксувати в коментарі, чому тут спільне джерело прийнятне.
confidence: medium
```

```
id: R-A2-11
severity: nit
where: pipeline/normalization/normalize.py:334, 360-383, 679
claim: Для ВСІХ полів type: number нормалізація обмежена 1..366 (MAX_PLAUSIBLE_DAYS) — межа днів вшита в генеричний тип.
evidence: normalize_field(number) → number_from_words → direct-шлях: return direct if 1 <= direct <= MAX_PLAUSIBLE_DAYS else None.
impact: Сьогодні всі number-поля — дні, тож ефекту нема; майбутнє поле «кількість осіб: 500» тихо стане прогалиною з причиною, що вказує не туди (виглядатиме як «не прочитано», а не «відсічено порогом днів»).
proposal: Зробити межі параметром поля схеми (min/max) з дефолтом для day-полів, або хоча б віддавати окрему причину out_of_declared_range у provenance.
confidence: medium
```

```
id: R-A2-12
severity: nit
where: pipeline/run.py:302-313 (_review_queue_type)
claim: Будь-який needs_review із фото отримує queue_type "handwritten", навіть коли причина не має стосунку до рукопису (морфологія ПІБ, непідтверджена критика на друкованому полі).
evidence: return "handwritten" if source_kind == "photo" else "unconfirmed_fact" — без перевірки, ЩО саме непідтверджене.
impact: Черга рев'ю в БД дає людині хибну підказку інструмента («дивись рукопис»), хоча проблема, напр., у відмінку прізвища; пріоритезація черги за типом стає шумною.
proposal: Ставити handwritten лише коли серед unknown-полів є поля, що на бланку заповнюються від руки (або коли провенанс укаже OCR-походження проблеми); інакше unconfirmed_fact і для фото.
confidence: medium
```

```
id: R-A2-13
severity: nit
where: pipeline/storage/local_store.py:81-96 (save)
claim: Запис файла та append в індекс не атомарні: збій між ними лишає збережений запис без рядка в індексі — наступний прогін створить ДРУГИЙ запис того самого документа з новим id.
evidence: save(): спершу open(path,"w").write(content), потім окремий open(index).append; жодного відкату/темп-файла.
impact: Після падіння процесу (світло, OOM від llama) той самий документ дає два записи в data/output і, після завантаження, два факти — рівно те подвоєння підрахунку, від якого дедуплікація мала захищати. Імовірність низька, наслідок — тихе подвоєння.
proposal: Писати запис у тимчасовий файл + os.replace, а рядок індексу — до/атомарно з перейменуванням; або при старті прогону звіряти documents/ з індексом і докидати відсутні хеші.
confidence: medium
```

## Що перевірено і НЕ є проблемою (щоб не шукали двічі)

- Порожній бланк: needs_review з чесними критичними прогалинами, друкований
  текст форми значенням не віддається (перевірено прогоном).
- Нормативні документи: procedural-гейт спрацьовує ДО вибору шаблону; і
  Статут, і обидві інструкції йдуть в unresolved із причиною, об'єкт не
  створюється (subject_kind: none / procedural_document:normative).
- Дедуплікація за хешем + `--reprocess`: працює, повторний прогін дає DUP.
- `declared-but-unread` ключі (`registry`, `multiple`, `out_of_scope`):
  валідатор попереджає вголос на кожному запуску — перевірено.
- Гейт морфології: `not_a_name`/`no_morphology`/`untagged_oblique` блокують
  confirmed через UNRELIABLE_MORPHOLOGY; already_nominative/untagged_name
  проходять — узгоджено з коментарями, статуси видно в provenance.
- `ground_llm_value`/`validate_block_value`/`validate_regex_value` — маршрути
  відхилення ведуть у global_gaps, значення зберігаються в
  unresolved_values/raw_text (перевірено по коду; LLM-прогони не запускались
  за правилами рецензії).
- Магічні пороги (MEGA_BLOCK_HEIGHT_RATIO 2.5, OVERSIZED 200, coverage 0.5,
  MIN_CUTTER_CHARS 8, llm_floor 2): кожен має в коментарі заміряні числа з
  обох боків розділення і тест (test_foreign_edition вимагає запас 0.2/2x) —
  це обґрунтовані межі, не підгонка.
- Підгонка під два бланки в .py мінімальна: доменні літерали живуть у
  schemas/*.yaml і dictionaries/*.yaml; винятки — R-A2-07/08/09.

## Примітка про побічний ефект рецензії

Для огляду записів виконано ОДИН персистентний прогін
`run_pipeline --no-llm --input data/eval/samples/leave/synthetic-2026-05/docx`:
у гітігнорованому `data/output/` з'явились 16 записів і 16 рядків індексу
дедуплікації. Якщо цей самий корпус планується класти в папку-приймач,
індекс віддасть duplicate — очистити `data/output/index/processed.jsonl`
або користуватись `--reprocess`.
