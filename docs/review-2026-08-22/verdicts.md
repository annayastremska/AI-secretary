# Адверсарна верифікація знахідок рев'ю 22.08.2026

Вхід: `arch.md` (A-01..A-20) і `code.md` (C-01..C-11), разом 31 знахідка від
двох сліпих рецензентів. Постава верифікатора — «це неправда, доведи»:
`confirmed` ставиться лише там, де доказ відтворений САМОСТІЙНО (репро-скрипт,
прямий виклик функції, прогін приладу), а не переказаний з `evidence`.

Репро-скрипти жили в scratchpad сесії, у репозиторій не кладуться. Нічого в
`pipeline/**` і `eval/**` не змінювалось; еталони не чіпались. HEAD на момент
перевірки — `2e344ba`, робоче дерево чисте.

Базові цифри переміряні на цьому ж HEAD і збігаються з тим, що заявляли
рецензенти (з поправкою на два нових тести від фіксу C-01):

```
python -m pytest eval/tests -q                                  -> 226 passed
python -m eval.evaluate --no-llm --input .../leave/.../docx     -> 224/224 (100.0%), шаблон 16/16, підтверджено 15 з 16
python -m eval.evaluate --no-llm --input .../deployment/.../docx-> 183/183 (100.0%), шаблон 14/14, підтверджено 13 з 14
```

## Підсумок

| | |
|---|---|
| confirmed | **29** |
| refuted | **1** (A-20) |
| unproven | **0** |
| duplicate | **1** (C-11 = A-15) |

Змінена severity: A-02 critical → should-fix (механізм латентний, не живий);
A-14 nit → should-fix (необроблений виняток валить увесь батч).

**Цінність сліпого рев'ю:** з 29 підтверджених **22 не мають жодного
відповідника** в `docs/known-weak-spots.md`, ще 2 покриті лише загальним
твердженням. Тобто ~76% підтверджених знахідок — нові.

## Зведення

| id | severity після перевірки | verdict | already_known | одним реченням |
|---|---|---|---|---|
| C-01 | critical | confirmed (виправлено) | no | Обидві половини закриті: квиток із двома маркерами акта лишається квитком, наказ № 280 лишається нормативним; третьої дірки на реальному корпусі немає (42 з 42 нормативних → `template=None`). |
| C-02 | critical | confirmed | no | «Порядок анульовання квитків» дійсно створює `supersedes` при `confirmed: True` — відтворено дослівно. |
| C-03 | critical | confirmed | no | Номер і дата чужого наказу підміняють реквізити з `matched`/0.9 і їдуть у БД окремими підтвердженими фактами — відтворено. |
| A-01 | critical | confirmed | no | Пайплайн, що НЕ підтверджує жодного документа, отримує ті самі 224/224 = 100.0% — переміряно. |
| A-12 | should-fix | confirmed | yes (8.5 п.1) | Переміряно самостійно: перефразування 13 з 29 друкованих рядків лишає покриття 0.778 і `recognized=True` — смуга 0.5…0.78 недосяжна за побудовою. |
| C-06 | should-fix | confirmed | no | Хибний ДОКАЗ порожнечі відтворено на живому коді: заповнене поле стає `confirmed_empty_slot:empty_pattern` через порожній примірник полів у тому ж документі. |
| C-05 | should-fix | confirmed | no | Сторінка PDF із 69 символами тексту І растром: `ocr_fn` не викликано, `ocr_pages=0`, `source_kind=electronic`, нуль попереджень. |
| C-07 | should-fix | confirmed | yes (5.3) | Заземлення пропускає 12/26/31/7 із «№ 4180/26 від 31.07.2026» — відсіює лише те, що не влазить у межі поля. |
| C-04 | should-fix | confirmed | no | Нормативний документ дає `status=confirmed, facts=[]`, а перевірка `чернетка_не_факт` на такому записі — 0/16. |
| C-08 | should-fix | confirmed | частково (розд. 3) | Поріг 20 символів без обґрунтування (сам weak-spots це визнає), `facts[0]` як основний факт — ніде не перевіряється. |
| A-02 | should-fix (було critical) | confirmed | yes (2.5) | `extra_subjects` справді не згадується в `run.py` ЖОДНОГО разу, але жодна схема не оголошує `group:`, тож дірка латентна. |
| A-03 | should-fix | confirmed | no | `facts.additional_info JSONB` існує, завантажувач її пише, а стале обґрунтування стоїть у схемах 9 разів. |
| A-04 | should-fix | confirmed | no | Видалення й підміна `normalization` на полях ПІБ дають ПОБАЙТОВО той самий вихід, валідатор — 0 помилок. |
| A-05 | should-fix | confirmed | no | `parse_rank_and_name('молодший сержант Гайдученко Остап Миронович')` → звання є, усі три частини ПІБ `None`. |
| A-06 | should-fix | confirmed | частково (2.12) | `load_ground_truth` читає лише `per-document/*.json`; у holdout-еталонах немає ключа `id`; `grep freeform eval/tests` = 0. |
| A-07 | should-fix | confirmed | no | Підтверджено сильніше за заявлене: `travel_document` (`permanent_event`) відсутній у копії лоадера, тобто вже зараз поїде як `ranged`. |
| A-08 | should-fix | confirmed | no | `identification.py` імпортує build_record, extract, blank_form, classify, normalize, subject_kind; два обходи циклу задокументовані в самому коді. |
| A-09 | should-fix | confirmed | no | Склад 224 переміряно точно: 178 польових + 32 прапорці + 14 порожніх; тривіальний `зв'язок_скасування` — 14 з 16 (не 12). |
| A-10 | should-fix | confirmed | yes (2.2) | LEAVE-013 і LEAVE-015 (`чинний=False`) виходять `status=confirmed, confirmed=True`, прилад цифру не змінює. |
| A-11 | should-fix | confirmed | no | Обидва `db_target: fact_value` поля — вільний текст без `category`; `category:` є рівно на `military_rank`. |
| A-14 | should-fix (було nit) | confirmed | no | `validate_schema` зі `blank_template` не-docx кидає `PackageNotFoundError` назовні — і .pdf, і .jpg. |
| A-13 | nit | confirmed | yes (2.7) | Валідатор дійсно віддає `error` на `multiple: true` без `deferred` — але це свідоме рішення, уже розібране. |
| A-15 | nit | confirmed | no | `main` завершується `return 0` беззастережно; єдиний ненульовий вихід — 2 «немає файлів». |
| A-16 | nit | confirmed | no | `leave_type.yaml` завантажується (`Довідники: ['leave_type','military_rank']`) і не використовується жодним `category:`. |
| A-17 | nit | confirmed | no | У `meta["identification"]` реального прогону ключі `['blank_edition','runner_up','score','source']` — `domain_scores` немає. |
| A-18 | nit | confirmed | no | README стверджує, що grep по postgres/psycopg нічого не дає; grep дає шість файлів. |
| A-19 | nit | confirmed | no | Ні `max_pages` у конфізі, ні посторінкового збереження; виняток у циклі → один `unresolved` на весь документ. |
| C-09 | nit | confirmed | no | `--dry-run` ставить `store=None`, а дедуплікація читається саме зі `store` — DUP у сухому прогоні неможливий. |
| C-10 | nit | confirmed | no | Порожній бланк: `create_subject_object: true` при повністю порожньому `subject`. |
| A-20 | none (знято) | **refuted** | — | Квадратичність у коді є, але `_accepted_spans` викликається ПО БЛОКУ: на найбільшому документі репо (402898 символів) резегментація — 0.122 с. |
| C-11 | — | **duplicate-of: A-15** | — | Те саме твердження про код виходу приладу. |

---

## По кожній знахідці

```
id: C-01
verdict: confirmed (виправлено комітами 7e3d0b8 + 2e344ba; третьої дірки на реальних даних не знайдено)
severity_after: critical (закрито)
already_known: no
how_verified:
  Репро на живому коді (`identify_template` з реальними схемами й доменами):
    LEAVE-001 чистий                              -> leave_ticket / leave
    LEAVE-001 + «НАКАЗУЮ:» + «набирає чинності»   -> leave_ticket / leave
    LEAVE-001 + «про внесення змін» + «цей порядок» -> leave_ticket / leave
    ті самі маркери ПЕРЕД текстом                  -> leave_ticket / leave
    Наказ МОУ № 280 (описує квиток)                -> None / normative, покриття 0.148
    інструкція_діловодство.docx                    -> None / normative, покриття 0.222
  Тобто перша половина (бланк не стає нормативним) і друга (нормативний акт,
  що описує бланк, не стає бланком) закриті обидві.
  ЗАМІР ПРОТИ ТРЕТЬОЇ ДІРКИ: прогнав identify_template на ВСІХ 42 нормативних
  файлах (docx + pdf) корпусу -- жодному не присвоєно шаблон (42 з 42
  `template=None`, `procedural_document:normative`).
  Прилад: 226 passed, leave 224/224, deployment 183/183 -- регресії немає.
note:
  Знайдено ОДИН синтетичний вхід, який гейт перевертає у зворотний бік:
  текст «нормативна шапка + ДОСЛІВНО відтворений порожній бланк квитка
  (як додаток)» дає покриття 1.000 і `template: leave_ticket`. Це НЕ третя
  половина C-01, а протилежний напрямок помилки, і він набагато дешевший:
  такий документ іде в needs_review з критичними прогалинами (людина його
  бачить), а не зникає тихо. Жоден реальний документ корпусу так не
  поводиться -- справжня Інструкція з діловодства, яка Додаток 30 містить,
  дає покриття 0.222, бо в її верстці рядки бланка розбиті інакше. Заводити
  окрему знахідку не варто; варто знати межу.
```

```
id: C-02
verdict: confirmed
severity_after: critical
already_known: no
how_verified:
  Репро: LEAVE-001.docx + один дописаний блок, далі повний ланцюг
  identify_template -> extract_document -> build_record (як у run.py).
    ''                                                | confirmed=True | links=[]
    'ВПД анульовано, видано нові.'                    | confirmed=True |
        links=[{link_type: supersedes, target_document_number: None,
                source_field: supersession_note, evidence: 'анульовано',
                method: 'matched'}]
    'Порядок анульовання квитків визначено інструкцією.' | confirmed=True |
        links=[{... evidence: 'анульовання'}]
  Провенанс поля в обох випадках: {method: matched, resolved: true,
  confidence: 0.9}, criticality: optional. Тобто запис іде в базу як
  повністю підтверджений і несе вказівку закрити чужий факт.
note:
  `known-weak-spots` розд. 2.2 стверджує протилежне -- «замір по всіх 60
  документах чотирьох корпусів: 0 хибних позитивів». Замір правильний, а
  висновок з нього -- ні: у корпусі просто немає документа зі згадкою цих
  слів поза справжньою позначкою. Це найдорожча знахідка списку разом із
  C-03: наслідок -- цифра «скільком людям зараз у відпустці» МЕНША за правду,
  і зіпсований лише зв'язок, тобто обидва записи в базі виглядають правильно.
```

```
id: C-03
verdict: confirmed
severity_after: critical
already_known: no
how_verified:
  Той самий ланцюг, LEAVE-001.docx з рядком «Відповідно до наказу командира
  № 777/К від 01.01.2020 р.», дописаним ПЕРЕД текстом документа:
    value[document_number] = "777/К"        (правильно: 102)
    value[document_date]   = "2020-01-01"   (правильно: 2026-05-09)
    prov[обидва] = {method: matched, resolved: true, confidence: 0.9}
    facts[0].confirmed = True, unknown_critical_fields = []
  Додатково перевірено те, чого рецензент не показав: підмінені значення
  доїжджають до БД ОКРЕМИМИ підтвердженими фактами --
    extra_facts: [... ('document_number', '777/К', True),
                      ('document_date', '2020-01-01', True) ...]
  тобто це не лише additional_info, а рядки таблиці facts із confirmed=true.
note:
  Правило узгодженості `not_before` справді односторонне й давнішу підставну
  дату пропускає (2026-05-10 >= 2020-01-01). Найсильніше твердження системи
  («прочитано з бланка») стоїть на `search()` по всьому тексту.
```

```
id: A-01
verdict: confirmed
severity_after: critical
already_known: no
how_verified:
  Репро: process_file на всіх 16 leave/docx, далі evaluate_record із реальним
  мапінгом і реальним еталоном, двічі -- з незміненою метою й з мутацією
  (status="needs_review", усі facts[*].confirmed=False):
    baseline                                  -> 224/224 = 100.0%
    усе needs_review, facts confirmed=False   -> 224/224 = 100.0%
  Тобто пайплайн, який НЕ підтверджує жодного документа, отримує ту саму
  максимальну оцінку. `чернетка_не_факт` у гілці «не confirmed» вимагає лише
  `not any(fact_flags)` (evaluate.py:665) -- масова відмова її ПРОХОДИТЬ.
  Рядок «підтверджено (основний факт): 15 з 16» -- print, у fields_total не
  входить (переміряно: 178+32+14 = 224, див. A-09).
note:
  `known-weak-spots` 2.15 закриває ДЗЕРКАЛЬНИЙ напрямок (прилад винагороджував
  вигадку), а цей напрямок (прилад не карає відмову) не записаний ніде.
```

```
id: A-02
verdict: confirmed
severity_after: should-fix (ЗНИЖЕНО з critical)
already_known: yes (2.5)
how_verified:
  `build_record` на LEAVE-001 повертає ключ `extra_subjects` (значення {}).
  `"extra_subjects" in open("pipeline/run.py").read()` -> False, тобто ключ
  не згадується в оркестраторі ЖОДНОГО разу: ні в base_meta, ні в blank_meta,
  ні у freeform. У реальному meta прогону (process_file на
  відпускний_шаблон.docx) `"extra_subjects" in meta` -> False.
note:
  Знижено, бо `grep -rn "group" pipeline/schemas/*.yaml` -> нуль: жодна
  наявна схема не оголошує `group:`, тому `extra_subjects` завжди {} і другої
  особи сьогодні не витягується ВЗАГАЛІ (не «витягується й губиться»).
  Дірка латентна -- вона відкриється рівно тоді, коли схема оголосить групу,
  і саме тому дешева зараз. Механізм покритий лише юніт-тестом
  (test_regressions.py:627), який працює на штучній схемі.
  Заразом: weak-spots 2.5 каже «лежить в extra_subjects» -- це неправда для
  ВИХОДУ пайплайна, ключа там немає; формулювання варто виправити.
```

```
id: A-03
verdict: confirmed
severity_after: should-fix
already_known: no
how_verified:
  Міграція `db/migrations/8a667569ba4d_facts_additional_info_jsonb.py:30` --
  `op.add_column("facts", sa.Column("additional_info", JSONB(), nullable=True))`.
  Завантажувач: `ai_secretary_loader.py:497` передає
  `additional_info=fact.get("additional_info")`, рядок 203 пише
  `Jsonb(additional_info)`. Тобто твердження схем «завантажувач її не читає,
  до бази не доходить узагалі (у facts немає JSON-колонки)» -- стале.
  Кількість сталих копій обґрунтування: `grep -c "у facts немає JSON-колонки"`
  -> deployment_certificate.yaml: 6, leave_ticket.yaml: 3, разом 9 (рецензент
  казав 8).
  Дублювання даних відтворено: на LEAVE-001 facts[0].additional_info містить
  document_number/document_date, і ті самі значення стоять окремими рядками в
  extra_facts.
note: —
```

```
id: A-04
verdict: confirmed
severity_after: should-fix
already_known: no
how_verified:
  Три прогони extract_document + build_record на LEAVE-001 з копіями схеми:
    базова схема                                   -> Лемешко / Соломія / Романівна
    `normalization` ВИДАЛЕНО з surname/given/patr  -> ідентично
    `normalization: null_if_not_issued` + sentinel  -> ідентично
  `validate_schema` у третьому випадку: 5 штатних повідомлень, жодного зі
  словом normalization, жодної помилки.
note: —
```

```
id: A-05
verdict: confirmed
severity_after: should-fix
already_known: no
how_verified:
  Прямий виклик із реальним довідником звань:
    parse_rank_and_name('молодший сержант Гайдученко Остап Миронович')
      -> ({'code': 'junior_sergeant', ...}, {'surname': None, 'given_name': None, 'patronymic': None})
    parse_rank_and_name('молодший сержант ГАЙДУЧЕНКО Остап Миронович')
      -> повний розбір
  Саме перший рядок надрукований у holdout-формі
  (довідка_лікування_01.expected.json, ключ `звання_та_піб`).
note:
  Поведінка НАВМИСНА й задокументована в докстрінгу (позиційний фолбек колись
  давав тихо зсунуті поля). Знахідка від цього не слабша: пропозиція
  рецензента (позиційний розбір із провенансом у UNRELIABLE_METHODS) якраз
  закриває причину, через яку фолбек прибрали. Оголошуваність правила
  (`surname_marker:`) -- окрема, дешева частина.
```

```
id: A-06
verdict: confirmed
severity_after: should-fix
already_known: частково yes (2.12 -- загальне «оцінка стоїть на синтетиці»)
how_verified:
  `load_ground_truth` (evaluate.py:86-92) читає рівно
  `glob(<eval-dir>/per-document/*.json)` і ключує за `data["id"]`.
  Holdout-еталон `довідка_лікування_01.expected.json` має верхні ключі
  `надруковано` / `правильні_відповіді` -- ключа `id` немає, у формат
  per-document він не потрапляє.
  `grep -rin freeform eval/tests` -> 0 рядків.
  Прогін process_file на holdout-документі без моделі:
    status=unresolved, template=None, reason='below_llm_floor',
    review_queue='unknown_type', subject_kind='unknown', facts=[]
  тобто гілка `_build_freeform_record` без моделі недосяжна взагалі й на
  CPU-прогоні не перевіряється нічим.
note: —
```

```
id: A-07
verdict: confirmed (сильніше, ніж заявлено)
severity_after: should-fix
already_known: no
how_verified:
  `validity_model` у pipeline згадується лише в докстрінгу
  `load_fact_types` (run.py:161-169); у facts і meta не потрапляє --
  `grep -rn validity_model pipeline/**/*.py` дає два рядки, обидва коментарі.
  Порівняв дві копії програмно (реєстр YAML проти FACT_TYPE_VALIDITY лоадера):
    у реєстрі 19 кодів, у лоадері 15;
    у реєстрі й не в лоадері: position, rank, travel_document, unrecognized;
    розходжень validity_model серед спільних: НЕМА.
  Далі перевірив, які коди оголошують САМІ СХЕМИ: 15 кодів, з них
  `position` і `travel_document` у копії лоадера відсутні. `rank`/`position`
  насіяні міграціями (1283dc745daa, 349d428a0094 ставить current_state), а
  `travel_document` -- ні. Реєстр каже `travel_document: permanent_event`,
  лоадер за замовчуванням поставить `ranged` (ai_secretary_loader.py:145).
note:
  Тобто передбачений рецензентом наслідок («permanent_event реквізит стає
  діапазонним фактом, який спливає») -- не гіпотеза, а ЖИВЕ розходження на
  одному конкретному коді. Це найсильніший аргумент за тест-порівнювач двох
  файлів, і він робить A-07 першим у черзі should-fix разом з A-12.
```

```
id: A-08
verdict: confirmed
severity_after: should-fix
already_known: no
how_verified:
  `pipeline/identification.py` (836 рядків) імпортує: build_record
  (CONSISTENCY_RULES, DERIVE_FUNCS), classify, blank_form, extract, normalize,
  subject_kind -- рядки 20-39. Обидва обходи циклу читаються в коді дослівно:
  `subject_kind.py:5-8` («identification.py уже імпортує build_record, тому
  будь-яке звернення звідти назад дало б цикл») і `blank_form.py:210`
  (`from pipeline.ingestion.ingest import extract_docx_blocks` УСЕРЕДИНІ
  функції `_read_lines`).
note:
  Це знахідка про структуру, не про поведінку -- поведінкового репро тут не
  існує за визначенням. Доказ (два задокументовані обходи циклу) відтворений
  читанням і достатній.
```

```
id: A-09
verdict: confirmed
severity_after: should-fix
already_known: no
how_verified:
  Переміряв склад 224 перевірок на leave/docx через per_key і checks:
    усього перевірок:                                   224
    чернетка_не_факт + зв'язок_скасування:               32
    expected_blank («очікується null»):                  14
    решта (польові):                                    178
    зв'язок_скасування на документі БЕЗ пари:        14 з 16
  Тобто 46 з 224 (20.5%) -- не поля, і рядок «без них: 210/210» знімає лише
  14 порожніх.
note:
  Одна поправка до evidence рецензента: тривіальних `зв'язок_скасування` не
  12, а 14 із 16 (пар у наборі чотири, зв'язок очікується від двох). Суть
  твердження від цього тільки сильніша.
```

```
id: A-10
verdict: confirmed
severity_after: should-fix
already_known: yes (2.2)
how_verified:
  Прогін 16 leave/docx, зріз по документах категорії «пара»:
    LEAVE-013 чинний=False status=confirmed confirmed=True
    LEAVE-014 чинний=True  status=confirmed confirmed=True
    LEAVE-015 чинний=False status=confirmed confirmed=True
    LEAVE-016 чинний=True  status=confirmed confirmed=True
  Головна цифра при цьому 224/224: жодна перевірка цього не бачить.
note:
  weak-spots 2.2 описує саму ситуацію («обидва документи пари виходять
  однаково confirmed») і обґрунтовує, чому закриття старого факту -- на боці
  БД. Нового тут рівно одне, і воно справедливе: ВІДСУТНІСТЬ цифри в приладі
  читається як відсутність проблеми. Агрегатна перевірка по корпусу -- на
  нашому боці й нікому не делегована.
```

```
id: A-11
verdict: confirmed
severity_after: should-fix
already_known: no
how_verified:
  Перебрав усі поля обох схем із `db_target: fact_value`:
    deployment_certificate / destination_points          type=text, category=None
    leave_ticket           / leave_type_and_destination   type=text, category=None
  Поля з `category:` у всьому репозиторії схем: рівно два, обидва `rank` ->
  `military_rank`. Довідники, завантажені прогоном: ['leave_type',
  'military_rank'].
note:
  A-16 -- підмножина цієї знахідки (той самий невикористаний довідник),
  лишаю окремо, бо дії різні: A-16 можна закрити видаленням файлу, A-11 --
  ні.
```

```
id: A-12
verdict: confirmed (і вперше ЗАМІРЯНО)
severity_after: should-fix (найвищий пріоритет у групі)
already_known: yes (8.5, п.1 -- дослівно той самий сценарій, лишений відкритим)
how_verified:
  Порогу 0.5 і розподілу з коментаря не переказував, а поставив замір, якого
  бракувало: беру ЗАПОВНЕНИЙ LEAVE-001 і перефразовую N найдовших друкованих
  рядків бланка (вставка одного слова в середину рядка -- саме та зміна, якою
  відрізняються редакції), далі `blank_edition_verdict`:
    друкованих рядків бланка: 29 (не 27)
    базове покриття LEAVE-001:                         0.926
    штучна «чужа редакція» з репо:                      0.148
    перефразовано  1 з 29 -> 0.926  recognized=True
    перефразовано  3 з 29 -> 0.889  recognized=True
    перефразовано  6 з 29 -> 0.889  recognized=True
    перефразовано 10 з 29 -> 0.815  recognized=True
    перефразовано 13 з 29 -> 0.778  recognized=True
  Тобто навіть перефразування ПОЛОВИНИ друкованих рядків не заводить документ
  у смугу 0.5…0.78, не кажучи про спрацювання правила. Смуга недосяжна не
  «випадково», а за побудовою мірки (вільні закінчення, різак від 8 символів).
note:
  Це підтверджує головний ризик A-12 цифрами: реальна інша редакція проходить
  як «форма впізнана», зі статусом confirmed і без жодного попередження.
  Пропозиція «не бінарний вердикт (recognized|partial|foreign)» тепер має
  замір під собою.
```

```
id: A-13
verdict: confirmed
severity_after: nit
already_known: yes (2.7 -- розібрано 14.08.2026, включно з рішенням зробити це помилкою)
how_verified:
  Копія схеми leave_ticket, у полі `co_travelers` прибрано `priority:
  deferred` й додано `multiple: true` -> `validate_schema` віддає
  severity=error: «multiple: true на невідкладеному полі -- рушій візьме лише
  ПЕРШЕ значення... Або priority: deferred, або поле не повинно бути multiple».
note:
  Твердження точне, але це свідоме, задокументоване рішення з обґрунтуванням
  («тихо неправильна цифра гірша за порожнє поле»), а не дефект. Нове тут
  лише спостереження, що межа лежить у мові схеми. Не робота, а абзац у
  документі архітектури.
```

```
id: A-14
verdict: confirmed
severity_after: should-fix (ПІДНЯТО з nit)
already_known: no
how_verified:
  Копія схеми з `blank_template`, що вказує на .pdf і на .jpg; виклик
  `validate_schema`:
    .pdf -> CRASH: PackageNotFoundError
    .jpg -> CRASH: PackageNotFoundError
  Виняток виходить із `_read_lines` (blank_form.py:210, безумовний
  `extract_docx_blocks`) назовні -- ні `validate_schema`, ні `build_resources`
  його не ловлять, тобто він доходить до `run_pipeline`.
note:
  Підняв severity, бо це прямо ламає інваріант, на якому стоїть уся
  валідація: «невалідна схема виключається, решта обробляється»
  (run.py:211-218). Одна одруківка в шляху -> нуль оброблених документів
  батчу й traceback без причини. Фікс -- try + error валідатора, кілька
  рядків.
```

```
id: A-15
verdict: confirmed
severity_after: nit
already_known: no
how_verified:
  `eval/evaluate.py`: `return 0` на 966 -- останній рядок main, беззастережно;
  єдиний інший вихід -- `return 2` на 795 («немає файлів»). `map_problems`
  лише друкуються в stderr (рядки 778-780), блоки «ПОЛЯ НА НУЛІ» і «ПОЛЯ
  НИЖЧЕ 50%» -- теж лише print. Переміряв на реальному прогоні: leave/docx і
  deployment/docx обидва дали exit 0.
note: C-11 -- та сама знахідка (див. нижче).
```

```
id: A-16
verdict: confirmed
severity_after: nit
already_known: no
how_verified:
  `grep "category:" pipeline/schemas` -> лише military_rank двічі; жодне поле
  не посилається на `leave_type`. При цьому прогін друкує
  `Довідники: ['leave_type', 'military_rank']`, і `res["dictionaries"]`
  реально містить обидва ключі (перевірено в інтерпретаторі).
note: —
```

```
id: A-17
verdict: confirmed
severity_after: nit
already_known: no
how_verified:
  Успішний return у `identify_template` (identification.py:746-752) ключа
  `domain_scores` не має; два інші return-и (713, 826) мають. Перевірено на
  об'єкті: `sorted(ident.keys())` для LEAVE-001 --
  ['blank_edition','domain','reason','runner_up','schema','score','scores','source','template'].
  У ЗБЕРЕЖЕНОМУ meta реального прогону:
  `meta["identification"].keys()` = ['blank_edition','runner_up','score','source'].
note: Один рядок роботи, як і сказано.
```

```
id: A-18
verdict: confirmed
severity_after: nit
already_known: no
how_verified:
  `pipeline/README.md:174-176` дослівно: «У цьому репозиторії немає коду
  підключення до бази даних — перевірено (`grep -r postgres/psycopg` по всьому
  проєкту не дав жодного результату)». Виконав цей самий grep зараз: шість
  файлів -- airflow/plugins/ai_secretary_loader.py, chat-mamaylm/db.py,
  demos/upload_app/chat.py, demos/upload_app/chat_gradio/db.py і дві міграції.
note: —
```

```
id: A-19
verdict: confirmed
severity_after: nit
already_known: no
how_verified:
  `ingest.py:249-296`: `for i, page in enumerate(doc)` рендерить у
  TemporaryDirectory і викликає ocr_fn посторінково, накопичуючи в один
  список; повернення -- лише в кінці. `grep -rn max_pages config.yaml
  config.example.yaml pipeline/` -> нуль входжень, тобто межі немає.
  Виняток будь-де в інжесті ловиться `except Exception` у process_file:576 і
  дає один запис `unresolved` на весь файл (код прочитано, гілка є).
note:
  Оцінку «години-доба на 200 сторінок» я НЕ перевіряв (це вимагало б прогону
  surya на штучному 200-сторінковому скані). Структурна частина твердження --
  усе-або-нічого, без межі, без прогресу -- підтверджена повністю.
```

```
id: A-20
verdict: refuted
severity_after: none (знято)
already_known: no
how_verified:
  Форма коду справді квадратична (`_accepted_spans`, blank_form.py:389-397:
  `any(...)` по вже прийнятих усередині подвійного циклу) -- це єдине, що в
  знахідці правильно. Але M у ній -- НЕ число збігів у документі: єдиний
  викликач `resegment_text` викликається `resegment_by_blank` ПО ОДНОМУ
  БЛОКУ (blank_form.py:540-542), тому M обмежене одним блоком, а не довжиною
  документа. Замір на найбільшому документі репо
  (інструкція_діловодство.docx, 402898 символів, 2230 блоків, 27 різаків):
    resegment_by_blank по блоках:              0.122 с (змінено 333 блоки)
    найдовший блок:                            2203 символи, 0 збігів різаків
    гіпотетичний виклик на ВСЬОМУ тексті одразу: 395 збігів, 0.029 с
note:
  Тобто передумова impact-а («на довгому документі M росте, і різання
  починає домінувати над самим OCR») хибна двічі: M не росте з довжиною
  документа за побудовою, а навіть найгірший гіпотетичний випадок -- 29 мс
  проти хвилин на кадр OCR. Роботи тут немає; варто лише не заводити цю
  правку в беклог.
```

```
id: C-04
verdict: confirmed
severity_after: should-fix
already_known: no
how_verified:
  Прогін process_file на normative/інструкція_діловодство.docx:
    status='confirmed', template=None, domain='normative',
    reason='procedural_document:normative', review_queue=None,
    subject_kind='none', create_subject_object=False, facts=[]
  Далі -- прилад на тих самих 16 leave/docx із мутацією мети:
    status='confirmed', facts=[]     -> чернетка_не_факт  0/16 (60/224 усього)
    status='needs_review', facts=[]  -> чернетка_не_факт 16/16 (76/224 усього)
  Тобто обидва боки твердження відтворені: прилад називає порушенням саме те,
  що пайплайн навмисно виробляє, і симетрично видає «ok» пайплайну, який на
  якомусь класі документів перестав віддавати факти взагалі.
note:
  Перетинається з A-01 (та сама гілка `not any([])`), але дія інша: тут
  потрібне явне розведення трьох станів (`facts_expected`), а не симетричний
  інваріант. Лишаю окремо.
```

```
id: C-05
verdict: confirmed
severity_after: should-fix
already_known: no
how_verified:
  Зібрав PDF на одну сторінку, у якій Є І текстовий шар, І растр (fitz:
  insert_text 69 символів + insert_image), і прогнав `load_document_blocks`
  з підставним ocr_fn-лічильником:
    сторінка: текст 69 символів; зображень 1
    ocr_fn викликано: 0 разів
    info: {'ocr_pages': 0, 'scan_pages_detected': False, 'source_kind': 'electronic'}
    warnings: []
    text: лише текстовий шар
  Растр загублено цілком, мовчки, і документ виглядає як звичайний
  `electronic`.
note:
  Друга, симетрична половина (`if not page.get_images(): continue` без
  попередження) у коді обґрунтована окремим коментарем (порожня сторінка
  docx-у, перейменованого в PDF) -- її я вважаю виправданою; знахідка
  тримається на ПЕРШІЙ половині, і та підтверджена повністю.
```

```
id: C-06
verdict: confirmed (репро сильніше за заявлене)
severity_after: should-fix
already_known: no
how_verified:
  1. Обіцянка існує дослівно: extract.py:1495-1498 -- «Межа чесності: сигнали
     вимагають ЛОКАЛІЗОВАНОГО слота. "Слот не знайдено" (no_label / no_value /
     чужа редакція бланка) лишається в LLM за побудовою».
  2. Реалізація П5-Б (extract.py:1770-1780) приймає ЛЮБУ причину, доки
     `value is None`, і питає `slot_is_provably_empty(field, ocr_text)`, тобто
     регекс по ВСЬОМУ тексту.
  3. Замір скелета порожнечі:
       порожній бланк                    -> 4 поля
       LEAVE-001 (заповнений)            -> 0 полів
       LEAVE-001 + порожній примірник    -> ті самі 4 поля
  4. НАСКРІЗНЕ репро (цього в звіті рецензента не було): LEAVE-001 з
     зіпсованим OCR + дописаний порожній примірник полів того ж бланка, повний
     ланцюг extract_document -> build_record:
       БЕЗ порожнього примірника: actual_return_date prov={method: matched, resolved: true}
       З порожнім примірником:    actual_return_date prov={method: 'confirmed_empty_slot:empty_pattern', resolved: false}
     Тобто поле, значення якого В ДОКУМЕНТІ Є (2026-05-23), отримало ДОКАЗ
     порожнечі. Це не гіпотеза «колись вистрілить» -- воно стріляє сьогодні,
     на двосторонньому бланку, який у знахідці й описаний.
note:
  Гейт `value is None` у коді свідомий і задокументований (коментар прямо
  згадує двосторонній бланк), але він захищає лише випадок, коли значення
  ПРОЧИТАНЕ. Коли читання зірвалось -- саме тоді, коли фолбек потрібен --
  доказ порожнечі його забирає. Мінімальний фікс із пропозиції (більше одного
  збігу скелета -> доказом не є) цей випадок закриває.
```

```
id: C-07
verdict: confirmed
severity_after: should-fix
already_known: yes (5.3, рядок про `днів = 143`)
how_verified:
  Прямий виклик `attested_numbers` / `ground_llm_value` на тексті
  «Відпускний квиток № 4180/26 від 31.07.2026. Терміном на дванадцять діб.»
  з полем {type: number, min_value: 1, max_value: 366}:
    attested: [7, 12, 26, 31, 2026, 4180]
    12   -> ('12', None)      31   -> ('31', None)
    26   -> ('26', None)       7   -> ('7', None)
    4180 -> (None, 'ungrounded_llm_value')
    2026 -> (None, 'ungrounded_llm_value')
    19   -> (None, 'ungrounded_llm_value')   # число, якого в документі немає
  Тобто заземлення відсіює лише те, що не влазить у межі поля або чого в
  документі немає взагалі; день, місяць і хвіст номера проходять.
note:
  weak-spots 5.3 фіксує один такий випадок (`днів = 143` -- номер документа --
  проходить заземлення й ловиться лише правилом узгодженості). Нове й слушне
  в C-07 -- висновок, що другий шар є ЗБІГОМ КОНФІГУРАЦІЇ: обов'язковості
  `consistency` для number-полів валідатор не вимагає. Це і є дія.
```

```
id: C-08
verdict: confirmed
severity_after: should-fix
already_known: частково (розд. 3, «Досі відкрито»: поріг 20 символів на сторінку підібраний на око -- дослівно); друга половина (facts[0]) -- no
how_verified:
  `run.py:594`: `if not text or len(text.strip()) < 20:` -- 20 літералом, без
  жодного рядка обґрунтування, поруч із порогами, у яких походження заміряне.
  `run.py:727`: `confirmed = bool(record["facts"]) and record["facts"][0].get("confirmed")`
  -- статус документа визначається позицією в списку. `grep` по
  `pipeline/identification.py` (валідатор): жодної перевірки порядку полів
  чи наявності основного факту немає.
note:
  Обидві половини правильні; перша вже визнана відкритою в weak-spots, друга
  ні. «Позначити основний факт явно» -- дешева правка, яка знімає мовчазний
  зв'язок через індекс між трьома модулями й чужим лоадером.
```

```
id: C-09
verdict: confirmed
severity_after: nit
already_known: no
how_verified:
  `run_pipeline.py:72-73`: `if args.dry_run: res["store"] = None`.
  `run.py:548`: `existing_key = store.find_by_hash(file_hash) if store else None`,
  далі `if existing_key and not reprocess: ... status="duplicate"`. Отже при
  `store=None` гілка `duplicate` недосяжна за побудовою: `--dry-run`
  еквівалентний `--reprocess` за побічним ефектом.
note:
  Той самий побічний ефект у приладі (`res["store"] = None`, evaluate.py:766)
  супроводжений коментарем, тут -- ні. Знахідка про честь інтерфейсу, не про
  дані; nit правильний.
```

```
id: C-10
verdict: confirmed
severity_after: nit
already_known: no
how_verified:
  Прогін process_file на leave/відпускний_шаблон.docx (порожній бланк):
    status='needs_review', template='leave_ticket',
    subject_kind='person', subject_kind_source='schema',
    create_subject_object=True, review_queue='unconfirmed_fact',
    subject={rank: null, surname: null, given_name: null, patronymic: null,
             person_alias: null, person_complete: false}
note:
  Логіка послідовна й у коментарі описана, шляху в базу немає (needs_review).
  nit підтверджений як nit.
```

```
id: C-11
verdict: duplicate-of: A-15
severity_after: nit
already_known: no
how_verified:
  Те саме твердження про `eval/evaluate.py:966` (`return 0`) і рядки 778-780
  (`map_problems` лише в stderr). Перевірено разом з A-15: обидва реальні
  прогони дали exit 0 при 224/224 і 183/183.
note:
  Пропозиції двох рецензентів складаються, а не суперечать: `--fail-under N`
  (C-11) плюс ненульовий код при map_problems / полях на нулі / провалених
  інваріантах (A-15). Виконувати як одну задачу.
```

---

## Вхід виконавця: підтверджене, за критичністю

### critical (в базу їде неправда, якої людина не побачить)

1. **C-02** — `supersession_note` шукає голе слово в усьому тексті:
   «Порядок анульовання квитків» дає `supersedes` при `confirmed: True`.
   Мінімум — межа слова й прив'язка до контексту; головне — `document_links`
   не має виходити з confirmed-документа без сигналу.
2. **C-03** — `document_number` / `document_date` беруться першим збігом у
   всьому тексті: чужий наказ підміняє реквізити з `matched`/0.9 і їде в
   `facts` окремими підтвердженими рядками. Мінімум — «більше одного збігу =
   `ambiguous`».
3. **A-01** — прилад не карає надмірну відмову: 100% при нулі підтверджених.
   Симетричний інваріант у `checks` + рядок `confirmed_rate`.

### should-fix, першими

4. **A-12** — поріг «інша редакція» недосяжний: заміряно, що перефразування
   13 з 29 друкованих рядків лишає `recognized=True` (0.778).
5. **A-07** — `validity_model` не їде з фактом, а копія лоадера ВЖЕ розійшлась
   на `travel_document` (permanent_event → ranged за замовчуванням).
6. **C-06** — хибний ДОКАЗ порожнечі відтворено на живому коді.
7. **C-04** — `confirmed` при `facts: []`: розвести три стани явно.
8. **C-05** — сторінка з текстом І растром губить растр мовчки.
9. **C-07** — заземлення числа по всьому документу; `consistency` для
   number-полів зробити обов'язковим у валідаторі.
10. **A-14** — не-docx `blank_template` валить увесь батч необробленим винятком.

### should-fix, далі

11. **A-03** — стале обґрунтування `dimension:` у 9 місцях; реквізити
    документа віддавати через `additional_info`.
12. **A-04** — `normalization` на полях ПІБ мовчки не діє; зробити оголошення
    помилкою валідатора.
13. **A-05** — прізвище лише у ВЕРХНЬОМУ регістрі; зробити правило
    оголошуваним + позиційний фолбек у UNRELIABLE_METHODS.
14. **A-06** — holdout не читається приладом, freeform без жодного тесту.
15. **A-09** — рахувати три цифри окремо (поля / інваріанти / порожні).
16. **A-10** — агрегатна перевірка пар по корпусу.
17. **A-11** — контрольований `value_code` для виду відпустки (або сказати в
    схемі, що він вільнотекстовий).
18. **A-08** — виділити `pipeline/schema_validation.py`.
19. **C-08** — обґрунтувати поріг 20; позначити основний факт явно.
20. **A-02** — провести `extra_subjects` у meta (латентно, але дешево).

### nit

21. **A-15 + C-11** — ненульовий код виходу приладу, `--fail-under`.
22. **A-17** — `domain_scores` в успішний return (один рядок).
23. **A-18** — прибрати grep-доказ із `pipeline/README.md`.
24. **A-16** — підключити або видалити `leave_type.yaml`.
25. **A-19** — `ocr.max_pages` + посторінковий прогрес.
26. **C-09** — розділити «сховище для читання» і «для запису» в `--dry-run`.
27. **C-10** — перейменувати `create_subject_object` або додати кон'юнкцію.
28. **A-13** — не робота; абзац у документі архітектури про межу мови схеми.

### не робити

- **A-20** — спростовано заміром (0.122 с на найбільшому документі репо).
