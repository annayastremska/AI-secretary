# Б2 — адверсарна верифікація знахідок рев'ю (14.08.2026)

Постава: **спростувати кожну**. Знахідка отримує `confirmed` лише там, де я САМ
відтворив доказ — прогоном, прямим викликом функції або прочитаним рядком коду
з цитатою. Переказ чужого evidence підтвердженням не вважався.

Вхід: `a1-architecture.md` (15), `a2-implementation.md` (13), `b1-stress.md` (5)
= 33 знахідки. Після злиття дублікатів — **27 унікальних**.

Що прогнано мною (усе — гілка `anya-pipeline`, робочий стан репо, `--no-llm`,
`pipeline/**` і `eval/**` НЕ редагувались, комітів немає; репро-скрипти — у
scratch-папці сесії):

| прогін | результат |
|---|---|
| `python -m pytest eval/tests -q` | 152 passed — базова лінія A1/A2 відтворена |
| `python -m eval.evaluate --input data/eval/samples/leave/synthetic-2026-05/docx --no-llm` | 176/176, шаблон 16/16, статуси `{confirmed: 15, needs_review: 1}` — відтворено |
| `repro_01_confirmed_writeback.py` | справжній `process_file` з підміною ОДНОГО вердикту (`ident.source="llm"` / `resolve_subject_kind → unknown`) |
| `repro_03_eval_blind.py` | той самий прилад над пайплайном, який КОЖЕН документ віддає як `needs_review` з `confirmed:true` |
| `repro_04_store_and_inputs.py` | сховище у scratch: reprocess, виняток у `process_target`, `document_links`, hold-out, docx під `.pdf` |
| зіпсовані docx у scratch | `31 лютого` і `двадцять одін` (копія LEAVE-001 з точковою заміною в `word/document.xml`) |

---

## Зведення

| id | severity після перевірки | verdict | already_known | одне речення |
|---|---|---|---|---|
| **R-A1-01** + **R-A2-01** | critical | confirmed | yes (8.6 — як КЛАС) | Відтворено: `template_by_llm` і `unknown_subject_kind` дають `status=needs_review` при `facts[*].confirmed=True` на всіх 8 фактах. |
| **R-B1-02** | critical | confirmed | no | Прямий виклик: 4-й токен ПІБ («кизи») зникає без жодного сигналу, `_leftover_before_surname=None`. |
| **R-A2-02** | should-fix (перший у черзі) | confirmed | yes (2.17) | Пайплайн, що ВСІ 16 документів віддає `needs_review` з `confirmed:true`, отримує ті самі 176/176 і 16/16. |
| **R-B1-03** | should-fix | confirmed | no (2.17 суміжний) | `number_from_words('двадцять одін') == 20`; наскрізно врятувала єдина перевірка `consistency_error: 20 != 13`. |
| **R-B1-04** | should-fix | confirmed | no | Наскрізно на «31 лютого»: `resolved:false` без `raw_text`, `unresolved_values: {}`, `warnings: []`. |
| **R-A1-02** | should-fix | confirmed | no | `_read_lines` неіснуючого шляху → `[]`, вердикт `recognized: True` при `total: 0`; валідатор молчить. |
| **R-A2-06** | should-fix | confirmed | no | Штучний виняток: `id=None`, нових файлів у сховищі 0, нових рядків індексу 0. |
| **R-A1-06** + **R-A2-05** | should-fix | confirmed | no | Одруківка `null_if_not_isued` не дає ЖОДНОГО повідомлення валідатора, а `«не видавались»` стає реальним значенням поля. |
| **R-B1-01** | should-fix | confirmed (гірше, ніж заявлено) | no | `classify_domain_rules` дає `leave` з балом **1**; додатково `subject_kind=person`, `create_subject_object=True`. |
| **R-A2-03** | should-fix | confirmed | yes (5.6 + розд. 7 A4) | Заміряно: 7 полів leave і 7 полів deployment не міряються, `check_mapping` зворотного проходу не має. |
| **R-A2-04** | should-fix | confirmed | no | `document_links` реально заповнені на LEAVE-014/016, а звіт дослівно друкує «пайплайн НЕ знає про скасування». |
| **R-A1-04** | should-fix | confirmed | no (2.9 — про чужий бік) | Після `--reprocess` на диску ДВА `.md` з тим самим `file_hash` і два рядки індексу; `delete`/`supersede` у сховищі немає. |
| **R-A1-08** | should-fix | confirmed | yes (2.8, 8.5) | `validate_block_value(surname, 'Звання', heads) → ('Звання', 'matched')`; схемного перевизначення констант немає. |
| **R-A1-09** | should-fix | confirmed | yes (2.8, 2.6) | `walk_tables`: `blocks.append(text)`; `SINGLE_VALUE_DB_TARGETS = {fact_value, fact_date_start, fact_date_end}`. |
| **R-A1-10** + **R-A2-12** | should-fix | confirmed | no | `_review_queue_type('needs_review','photo') → 'handwritten'` без жодної перевірки вмісту. |
| **R-A1-03** + **R-A2-11** | should-fix (латентно) | confirmed | no | `normalize_field(type=number, '500') → None`, `'сто двадцять' → None`; межа днів у генеричному типі. |
| **R-A1-05** + **R-A2-09** | should-fix | confirmed | no | Той самий літерал-словник у `evaluate.py:353` і `:567`; `DOC_ID_RE` на `LEAVE|TRIP`. |
| **R-A1-07** | nit (знижено) | confirmed | no | Імпорти справді такі, як заявлено, але заміряного наслідку немає — це структура, не дефект. |
| **R-A1-11** | nit | confirmed | no | Нуль входжень `first_block_matching`/`starts_with` у `pipeline/schemas/*.yaml`. |
| **R-A1-12** | nit | confirmed (часткова перевірка) | no | `self_consistency_n: 1` у дефолтах, `majority_vote(['a']) → ('a', False)` — `llm_split_vote` недосяжний; хронометраж я НЕ переміряв. |
| **R-A1-13** | nit | confirmed | no | `_persist` — рядок 622 усередині `process_file`; `meta["archived_to"]` — рядок 739, уже після повернення. |
| **R-A1-14** | nit | confirmed | no | Ваги 3/1 проти `TITLE_WEIGHT=5`/`ANCHOR_WEIGHT=2`; фраза «після закінчення строку відпустки» лежить у ДВОХ джерелах. |
| **R-A1-15** + **R-A2-13** | nit | confirmed | no | `save()`: `open(path,"w").write(...)`, потім окремий append в індекс — ні tmp+rename, ні відкату. |
| **R-A2-07** | nit | confirmed | no | `"термін не розпізнано"` продукується в `normalize.py:431`, порівнюється літералом у `build_record.py:341`. |
| **R-A2-08** | nit | confirmed | no | `re.sub(r"\s*за\s*\d{4}\s*рік...")` стоїть усередині генеричного `match_dictionary`. |
| **R-A2-10** | nit | confirmed | no | `RANK_ALIASES = res["dictionaries"].get("military_rank")` — спільне джерело, поруч із власною копією гомогліфів. |
| **R-B1-05** | nit | confirmed | no | Копія LEAVE-001.docx під іменем `.pdf`: `status=confirmed`, `source_kind=photo`, значення правильні. |

**Спростованого немає.** Один суб-теза A2-01 (про `foreign_edition`) — `unproven`,
див. нижче.

---

## По кожній знахідці

```
id: R-A1-01 + R-A2-01  (ЗЛИТО — одна знахідка, два id)
verdict: confirmed
severity_after: critical
already_known: yes (розд. 8.6 — там прямо написано «Клас той самий, що вже
  існує для template_by_llm і unknown_subject_kind», і закрито його ЛИШЕ для
  UNVERIFIED_METHOD)
how_verified: repro_01_confirmed_writeback.py — справжній process_file на
  LEAVE-001.docx з підміною РІВНО одного вердикту (пайплайн не редагувався):
    базова лінія          -> status=confirmed,    facts=[True]*8
    ident.source="llm"    -> status=needs_review, review_reason=template_by_llm,
                             review_queue=unknown_type, unknown_critical=[],
                             facts[0].confirmed=True, усі 8 фактів True
    resolve_subject_kind  -> status=needs_review, review_reason=unknown_subject_kind,
      -> unknown             review_queue=unknown_type, facts=[True]*8
  Механізм у коді: run.py:488 кладе результат у ЛОКАЛЬНУ змінну `confirmed`,
  рядки 500/512/555 гасять її, а на 604 у meta їде `record["facts"]` таким, як
  його зібрав build_record.py:564 (`"confirmed": len(unknown_critical_fields)==0`).
note: суб-теза A2-01 про foreign_edition — unproven: на єдиному наявному
  артефакті (`data/eval/samples/leave/відпускний_квиток_інша_редакція.docx`,
  --no-llm) факт чесно виходить `confirmed:false` (усі критичні поля -> no_label
  або unverified_foreign_edition, який уже в UNRELIABLE_METHODS після 8.6).
  Схеми, у якої ВСІ критичні поля з `label_before`, у репо не існує, тому цю
  гілку я відтворити не можу. Корінь у неї той самий, і виправлення (запис
  фінального confirmed у facts) закриває її разом з рештою.
outcome: fixed — run.py після всіх гейтів (template_by_llm / чужа редакція /
  unknown_kind) пише фінальний confirmed назад у КОЖЕН fact. Тест на тишу
  (репро repro_01, monkeypatch ident.source="llm"):
  eval/tests/test_review_fixes.py::test_needs_review_gate_writes_back_into_facts.
  ДО: needs_review + facts=[True]*8; ПІСЛЯ: needs_review + facts=[False]*8.
  Корпуси: leave docx/pdf 192/192, deployment docx/pdf 169/169, тестів 159.
```

```
id: R-B1-02
verdict: confirmed
severity_after: critical
already_known: no (2.1 говорить про person_alias як канонічний рядок, але про
  обрізання частини імені не згадує)
how_verified: прямий виклик з реальним довідником звань:
  parse_rank_and_name('рядовий ЛЕМЕШКО Соломія Мустафа кизи', ranks)
    -> ({'code':'soldier',...}, {'surname':'ЛЕМЕШКО','given_name':'Соломія',
        'patronymic':'Мустафа','_leftover_before_surname': None})
  Побайтово те саме, що для входу БЕЗ «кизи» — тобто відрізнити ці два входи за
  виходом функції неможливо. Рядки extract.py:1176-1185: `after[0]` / `after[1]`
  беруться позиційно, `after[2:]` не читається ніде, а симетричний сигнал є лише
  для токенів ЛІВОРУЧ (`_leftover_before_surname`).
note: severity критична саме через асиметрію — код сам визнає (той самий
  докстрінг), що позиційний фолбек дає «не порожні, а тихо неправильні» поля, і
  для лівого боку сигнал зробив, а для правого ні.
outcome: fixed — parse_rank_and_name повертає симетричний сигнал
  `_leftover_after_patronymic`; extract_document при хвості не вирішує
  patronymic мовчки, а віддає (None, "name_tail_unparsed:<повний хвіст>"),
  рядок іде в підказку LLM (localized_gaps), build_record кладе хвіст у
  unresolved_values/raw_text. Критичне поле блокує confirmed -> needs_review.
  ДО: вихід для «Мустафа кизи» і «Мустафа» побайтово однаковий, confirmed.
  ПІСЛЯ: patronymic нерозв'язаний, хвіст видно рев'юерові. Тести:
  test_review_fixes.py (3 тести). Корпуси: 192/192, 192/192, 169/169, 169/169.
```

```
id: R-A2-02
verdict: confirmed
severity_after: should-fix (але ПЕРШИЙ у черзі should-fix)
already_known: yes (розд. 2.17 — «Міряли половину відповіді», там же сказано,
  що перевірка позначки додана РІВНО для swapped_dates)
how_verified: repro_03_eval_blind.py — той самий прилад над пайплайном, у якому
  identify_template віддає source="llm" на КОЖНОМУ документі:
    статуси: {'needs_review': 16}   (було {confirmed: 15, needs_review: 1})
    LEAVE-012: status=needs_review  confirmed=True
    усього полів правильно: 176/176 (100.0%)
    шаблон визначено правильно: 16/16
  Тобто повний розрив «чернетка ≠ факт» на всіх 16 документах приладом
  невидимий. Код: у evaluate_record єдиний check зі `compare: "flag"` —
  `суперечність_діапазону` (evaluate.py:554); `confirmed`, `status`,
  `review_queue`, `unknown_critical_fields` повертаються як поля звіту, поза
  `checks`, тому в `fields_ok/fields_total` не входять.
note: це і є причина, чому R-A1-01/R-A2-01 могла дожити до сьогодні — прилад
  її не карає. Виправляти варто в парі з нею.
outcome: fixed — у evaluate_record додано перевірку «чернетка_не_факт»
  (compare: flag, входить у чисельник/знаменник): status=confirmed вимагає
  всі facts.confirmed=true і порожній unknown_critical_fields; інші статуси —
  жодного confirmed=true. Тести на тишу: eval/tests/test_review_fixes.py.
  ДО: leave 176/176, deployment 155/155 (розрив статусів невидимий).
  ПІСЛЯ: leave docx/pdf 192/192, deployment docx/pdf 169/169, тестів 157.
```

```
id: R-B1-03
verdict: confirmed
severity_after: should-fix
already_known: no (2.17 «Побічне спостереження» — про abs() у
  _days_span_inclusive, інший механізм; часткова сума там не згадана)
how_verified: прямі виклики:
  number_from_words('двадцять одна')  -> 21   (правильно)
  number_from_words('двадцять одін')  -> 20   <- часткова сума
  number_from_words('двадцять фыва')  -> 20
  number_from_words('двадцять о́дна')  -> 20
  Наскрізно: копія LEAVE-001 з «тринадцять» -> «двадцять одін» (scratch),
  process_file --no-llm: duration_days method=matched, confidence=0.9,
  consistency_problems={'duration_days': 'consistency_error: 20 != 13'},
  status=confirmed, facts[0].confirmed=True.
  Код normalize.py:376-383: `for token in tokens[:2]` ... `break` ...
  `return total if matched` — саме `if matched`, не `if matched == len(tokens)`.
note: підтверджую і одношаровість: значення 20 з провенансом matched/0.9
  зупинила РІВНО одна перевірка `days_span_inclusive`; поле некритичне, тому
  сам документ лишився confirmed.
outcome: fixed — number_from_words повертає значення лише коли РОЗПІЗНАНО
  ВСІ токени входу (і їх не більше двох); нерозпізнаний токен -> None.
  ДО: 'двадцять одін' -> 20 (matched/0.9). ПІСЛЯ: None (чесна прогалина).
  'двадцять одна' -> 21, 'тринадцять' -> 13 не зачеплені. Тести:
  test_review_fixes.py (2). Корпуси: 192/192 ×2, 169/169 ×2, тестів 164.
```

```
id: R-B1-04
verdict: confirmed
severity_after: should-fix
already_known: no
how_verified: прямі виклики + наскрізний прогін.
  normalize_date('31','лютого','2026') -> None; ('31','січня','2026') -> 2026-01-31
  parse_date_from_text('«31» лютого 2026 р.') -> {'day':'31','month':'лютого','year':'2026'}
  Наскрізно (копія LEAVE-001 з «з “10” травня» -> «з “31” лютого», --no-llm):
    status=needs_review, unknown_critical=['leave_start_date']
    prov[leave_start_date] = {'method':'matched','criticality':'critical','resolved':False}
    unresolved_values = {}   warnings = []
  Причина, якої в A1/B1 немає: механізм `unresolved_values` існує
  (build_record.py:456-465), але вимагає `isinstance(raw_value, str)` — а
  date-поле приходить з regex ГРУПАМИ (dict day/month/year), тому сирий збіг
  не зберігається ніде. Це робить пропозицію B1 точнішою, а не слабшою.
note: додатковий побічний ефект, який B1 не назвав: через відсутню дату
  duration_days отримує `unverifiable_dependency`, тобто одна описка гасить два
  поля, і жодне з них не каже, ЧОМУ.
outcome: fixed — build_record тепер зберігає сирий збіг і для raw_value-dict
  (regex-групи дати склеюються в рядок «31 лютого 2026» -> unresolved_values
  + provenance.raw_text). ДО: resolved:false без сліду. ПІСЛЯ: рев'юер бачить,
  що стояло в документі. Тест: test_impossible_date_keeps_raw_match_visible.
  Корпуси: 192/192 ×2, 169/169 ×2, тестів 165.
```

```
id: R-A1-02
verdict: confirmed
severity_after: should-fix
already_known: no (2.11a фіксує припущення про ТЕКСТ бланка, не про відсутній
  ФАЙЛ; 8.5 — про поріг, не про шлях)
how_verified: прямі виклики:
  blank_form._read_lines('data/nope.docx') -> []
  blank_edition_verdict(текст, схема з blank_template='.../НЕМА.docx')
    -> {'found': 0, 'total': 0, 'coverage': None, 'threshold': 0.5,
        'recognized': True}      <- форма визнана впізнаною при нулі доказів
  validate_schema(схема з битим шляхом) — РІЗНИЦЯ з базовим прогоном
    порожня: жодного error, жодного warning.
  grep: `BLANK_TEMPLATE_KEY` є лише в blank_form.py, в identification.py нуль
  входжень — тобто валідатор про цей ключ не знає взагалі.
note: `recognized: True` при `total: 0` — це «не довіряти нема підстав», що для
  відсутнього файлу означає тиху втрату трьох захистів одразу (резегментація
  фото 2.11a, printed_form_text 5.9, вердикт редакції розд. 8).
outcome: fixed — два шари: (а) validate_schema еррорить оголошений
  blank_template з неіснуючим шляхом або без жодного друкованого рядка;
  (б) blank_edition_verdict при оголошеному шаблоні й total==0 дає
  recognized:False + reason=blank_template_missing_or_empty, run.py пише
  окреме попередження й не дає confirmed. Схема БЕЗ ключа лишається інертною
  (оголошена межа). ДО: recognized:True при нулі доказів, валідатор мовчить.
  Тести: 3. Корпуси: 192/192 ×2, 169/169 ×2, тестів 172.
```

```
id: R-A2-06
verdict: confirmed
severity_after: should-fix
already_known: no (2.18 — про втрату документів через OCR/сервер, інший шлях;
  коментар run.py:103-106 описує цей клас для помилок СХЕМИ, не для except у
  process_target)
how_verified: repro_04, підмінений process_file кидає RuntimeError:
  results[0] = {'id': None, 'status': 'unresolved', 'file_hash': None,
                'storage_key': None, 'review_queue': 'unknown_type',
                'reason': 'необроблена помилка: RuntimeError: ...'}
  нових файлів у сховищі: set()      нових рядків індексу: 0
  Тобто сліду немає ніде, крім консолі; `continue` до того ж минає
  archive_input_file, тож файл лишається в папці-приймачі.
note: підтверджую і «вічний цикл»: без архівації і без рядка в індексі той
  самий файл падатиме на кожному наступному запуску.
outcome: fixed — except-гілка process_target тепер створює повний запис
  (id=uuid, file_hash, _persist у сховище + рядок індексу) і НЕ минає
  архівацію: файл їде у failed_dir. Збій самого _persist не валить батч,
  а пишеться у warnings запису. Тест:
  test_crashed_document_is_persisted_and_archived (штучний RuntimeError,
  сховище в tmp). Корпуси: 192/192 ×2, 169/169 ×2, тестів 173.
```

```
id: R-A1-06 + R-A2-05  (ЗЛИТО — одна знахідка, два id; A2-05 містить наслідок,
  A1-06 — мертвий ключ iso_date)
verdict: confirmed
severity_after: should-fix
already_known: no (2.7 закриває інші нечитані ключі — multiple/registry/
  out_of_scope; значення ключа `normalization` там не згадане)
how_verified:
  1) grep "iso_date" по pipeline/**/*.py — НУЛЬ входжень; у схемах 4 рядки в
     leave_ticket.yaml + 4 в deployment_certificate.yaml (A1 писав «7 разів» —
     фактично 8 рядків, суті не змінює).
  2) Диспетчеризація: normalize_field перевіряє `type == "date"` (рядок 683)
     ДО будь-якої згадки `normalization`, читаються лише "nominative_case" і
     "null_if_not_issued".
  3) Одруківка: у копії leave_ticket замінив `null_if_not_issued` ->
     `null_if_not_isued` (поле travel_document_number, type text,
     not_issued_sentinel «не видавались»). РІЗНИЦЯ у виході validate_schema —
     порожня.
  4) Наслідок прямим викликом:
       normalize_field(правильне поле, 'не видавались') -> (None, True)
       normalize_field(з одруківкою,   'не видавались') -> ('не видавались', False)
     і is_placeholder('не видавались', field_placeholder_tokens) -> False, тобто
     сентинел справді виключений з placeholder-ів і рядок піде як РЕАЛЬНЕ
     значення поля з dimension: travel_document.
note: severity should-fix, а не nit, саме через (4): підтверджено-порожнє поле
  перетворюється на текстове значення, що їде в базу окремим фактом.
outcome: fixed — (а) 8 мертвих рядків `normalization: iso_date` знято зі схем;
  (б) validate_schema перевіряє normalization як закритий перелік
  (KNOWN_NORMALIZATIONS), еррорить мертвий ключ на type date/number/category,
  null_if_not_issued без сентинела і сентинел без null_if_not_issued.
  ДО: одруківка -> тиша, «не видавались» їде в БД. ПІСЛЯ: error, схема
  виключається вголос. Тести: 4 у test_review_fixes.py.
  Корпуси: 192/192 ×2, 169/169 ×2, тестів 169.
```

```
id: R-B1-01
verdict: confirmed (сильніше, ніж заявлено)
severity_after: should-fix
already_known: no
how_verified: repro_04, усі 4 файли hold-out через process_file --no-llm:
  status=unresolved, reason=below_llm_floor, identification.scores
  {deployment_certificate: 0, leave_ticket: 0} — і при цьому
  domain='leave', subject_kind='person', create_subject_object=True.
  Бал домену переміряний окремо на тексті довідки (paragraphs + таблиці):
  classify_domain_rules -> ('leave', {'leave': 1, 'deployment': 0,
  'equipment': 0, 'staffing': 0}) — тобто РІВНО один збіг body-фрази.
  Код: `if best_score == 0 or best_score == runner_up_score: return None` —
  мінімального порога для тематичного домену немає, 1 достатньо.
note: B1 назвав лише `domain`. Насправді той самий один збіг тягне ще й
  `create_subject_object: True` через domain_map — тобто медична довідка
  приносить дозвіл створити об'єкт-особу в чужому реєстрі. Бал домену в записі
  дійсно не друкується (в `identification` лежать лише скори ШАБЛОНІВ).
outcome: fixed — (а) тематичному домену потрібен бал >= 2
  (DEFAULT_DOMAIN_MIN_SCORE, перевизначається ключем `min_score` домену в
  довіднику): заголовок (×3) проходить сам, body-фраз треба щонайменше дві;
  (б) скори доменів тепер їдуть у identification.domain_scores unresolved-
  запису — вирок перевірний. ДО: 1 збіг -> leave + person +
  create_subject_object. ПІСЛЯ: domain=None, kind=unknown, об'єкт не
  створюється. Тести: 3. Корпуси: 192/192 ×2, 169/169 ×2, тестів 176.
```

```
id: R-A2-03
verdict: confirmed
severity_after: should-fix
already_known: yes (5.6 — basis_order_* 0/14 непомітно; розд. 7 «Лишається
  відкритим», A4 — прямий запит додати position_and_workplace у мапінг)
how_verified: власний прохід «поля схеми проти field-mapping.yaml» (з
  урахуванням блоку person і виключенням priority: deferred):
  leave_ticket НЕ міряються: surname, given_name, patronymic (покриті
    опосередковано через person_alias/ПІБ), leave_year, travel_document_number
    (dimension: travel_document), supersedes_document_number, supersession_note
  deployment НЕ міряються: surname, given_name, patronymic, basis_order_date
    (dimension: order_date), basis_order_number (dimension: order_number),
    supersedes_document_number, supersession_note
  Перелік A2 збігається з заміром точно. check_mapping (evaluate.py:342-427)
  має лише ПРЯМІ перевірки оголошених ключів (check_answered, check_printed,
  field-у-схемі, range_checks) — зворотного проходу «поле схеми без мапінгу»
  немає ні рядка, і в базовому прогоні жодного [МАПІНГ]-рядка не друкується.
outcome: fixed — check_mapping отримав зворотний прохід: активне поле схеми
  мусить або мірятись ключем мапінгу, або стояти в новому розділі
  `unmeasured:` З ПРИЧИНОЮ (застарілий запис теж помилка). 6+7 полів
  оголошено свідомо неміряними з причинами; travel_document_number СТАВ
  мірятись (ключ «впд», printed VPD + printed_not_issued-сентинел у мапінгу).
  Нова перевірка одразу зловила реальний баг: текстовий шар PDF рве сентинел
  («не \nвидавались» -> «не» їхало в БД значенням ВПД) — виправлено
  багаторядковим варіантом патерна в схемі + пробіло-стійким порівнянням
  сентинела. Тести: 4. Корпуси: leave docx/pdf 208/208 (було 192, ДО фіксу
  PDF показував 200/208 — прилад бачить), deployment 169/169 ×2, тестів 181.
```

```
id: R-A2-04
verdict: confirmed
severity_after: should-fix
already_known: no (2.2 фіксує, що ознака витягується; про сліпоту ПРИЛАДУ там
  нічого)
how_verified: repro_04, process_file --no-llm:
  LEAVE-013: document_links=[]
  LEAVE-014: [{'link_type':'supersedes','target_document_number':None,
               'source_field':'supersession_note','evidence':'перервана',
               'method':'matched'}]
  LEAVE-016: [{... 'target_document_number':'157' ...}, {... 'анульованого' ...}]
  Водночас базовий прогін приладу друкує дослівно:
    «(пайплайн НЕ знає про скасування -- це очікувано, зв'язку
      документ->документ у контракті немає)»
  grep `document_links` по eval/evaluate.py — нуль входжень.
note: підтверджую і другу половину: стейл-текст не просто не міряє, а активно
  переконує читача, що міряти нічого.
```

```
id: R-A1-04
verdict: confirmed
severity_after: should-fix
already_known: no — 2.9 стосується ЧУЖОГО боку (ON CONFLICT DO NOTHING у
  завантажувачі); подвоєння записів на НАШОМУ диску там не описане
how_verified: repro_04 зі сховищем у scratch:
  прогін 1 -> confirmed, id 480e2c39...
  прогін 2 (без reprocess) -> duplicate
  прогін 3 (--reprocess)   -> confirmed, id 28c2fbe1...
  на диску: documents/leave/480e2c39....md І documents/leave/28c2fbe1....md
  індекс: ДВА рядки з ОДНИМ file_hash (ae31f4de...), find_by_hash віддає новий
  у старому записі: status=confirmed, facts=8, той самий file_hash, жодного
    ключа з 'supersed'/'reprocess'
  публічний інтерфейс сховища: ['find_by_hash','index_path','key_for','root',
    'save'] — ні delete, ні supersede
note: A1 писав «індекс перезаписує hash→key» — фактично індекс ДОПИСУЄ другий
  рядок (append), а перемагає останній при читанні. Наслідок той самий,
  механізм трохи інший.
outcome: fixed — store.retire(key) переносить старий запис у superseded/
  (дані не губляться, живим лишається один запис на вміст); process_file при
  reprocess кладе ключ старого запису в meta.supersedes_storage_key, _persist
  ретирить старий ПІСЛЯ успішного збереження нового. Індекс лишається
  append-only (перемагає останній рядок). ДО: два .md у documents/ з одним
  file_hash. ПІСЛЯ: один живий + один у superseded/. Тест:
  test_reprocess_retires_previous_record. Корпуси: 192/192 ×2, 169/169 ×2,
  тестів 177.
```

```
id: R-A1-08
verdict: confirmed
severity_after: should-fix
already_known: yes (2.8 — саме MIN_LABEL_HEAD_CHARS=16 і шапка «Звання»;
  8.5 — межа виведена з нашої ж перефразовки)
how_verified: прочитані рядки з цитатою + власний прямий виклик:
  extract.py:157 OVERSIZED_CANDIDATE_CHARS = 200 («Легітимні багаторядкові
    значення бланків... лишаються в межах з великим запасом»)
  extract.py:896-903 LABEL_HEAD_TOKENS = 3, MIN_LABEL_HEAD_CHARS = 16 («жоден
    лейбл двох наявних схем...»)
  extract.py:236-241 «у цій родині бланків лейбл-примітка в дужках
    СИСТЕМАТИЧНО стоїть ПІД значенням»
  Наслідок відтворив сам: validate_block_value(поле surname, 'Звання',
    schema_label_heads(leave_ticket)) -> ('Звання', 'matched')
    (голови лейблів: 'військове звання, прізвище,' тощо — 6-символьна шапка в
     них не входить за побудовою).
  Схемного перевизначення немає: grep по pipeline/schemas/*.yaml на
  oversized/label_head/name_format — нуль; при цьому min_score,
  llm_floor, min_blank_coverage схемою перевизначаються (identification.py:
  87 MIN_BLANK_COVERAGE_KEY, :525 min_score). Патерн існує й застосований не всюди.
```

```
id: R-A1-09
verdict: confirmed
severity_after: should-fix
already_known: yes (2.8 — дослівно `ingestion/ingest.py:walk_tables ->
  blocks.append(text)`; 2.6 — один суб'єкт і один рядок на документ)
how_verified: прочитано ingest.py:86-97 — у walk_tables є `cell`, `row`,
  `table` і навіть `tc = cell._tc`, а в blocks їде `blocks.append(text)`:
  індекси (таблиця, рядок, стовпець) фізично в руках і відкидаються.
  Прямий виклик: identification.SINGLE_VALUE_DB_TARGETS ==
  {'fact_value','fact_date_start','fact_date_end'} — по одній змінній на таргет.
note: severity лишаю should-fix попри «вже відомо»: пропозиція A1 (перестати
  викидати індекси, нічого не ламаючи) — найдешевший перший крок із усіх, що
  запропоновані для цього класу, і вона НЕ дублює план з
  2026-08-14_multirow-tables.
```

```
id: R-A1-10 + R-A2-12  (ЗЛИТО)
verdict: confirmed
severity_after: should-fix (A2 ставив nit — беру вищу з двох, вона підтверджена)
already_known: no
how_verified: прямий виклик:
  _review_queue_type('needs_review','photo')      -> 'handwritten'
  _review_queue_type('needs_review','electronic') -> 'unconfirmed_fact'
  Код run.py:312 — один рядок, жодної перевірки вмісту чи причини.
  Незалежно перетинається з R-B1-05: docx під іменем .pdf отримує
  source_kind=photo, тобто «рукописним» може стати навіть born-digital файл.
```

```
id: R-A1-03 + R-A2-11  (ЗЛИТО)
verdict: confirmed
severity_after: should-fix, латентно (A1: should-fix, A2: nit — беру вищу,
  але прямо кажу, що на нинішніх корпусах наслідку НЕМА: усі number-поля — дні)
already_known: no
how_verified: прямі виклики з полем {type: number, db_target: additional_info}:
  '500' -> None      'сто двадцять' -> None      '367' -> None
  '120' -> 120       '366' -> 366                'тринадцять' -> 13
  normalize.MAX_PLAUSIBLE_DAYS == 366; UKR_NUMBER_WORDS закінчується на
  «тридцять» (максимум комбінації — 31).
note: підтверджую і форму провенансу — таке поле виглядає як «не прочиталось»,
  а не «відсічено порогом днів»; окремої причини немає.
```

```
id: R-A1-05 + R-A2-09  (ЗЛИТО)
verdict: confirmed
severity_after: should-fix (A2 ставив nit — беру вищу)
already_known: no
how_verified: grep + читання:
  evaluate.py:40  DOC_ID_RE = re.compile(r"((?:LEAVE|TRIP)-\d+)", re.I)
  evaluate.py:353 tpl_of = {"відпускний квиток": "leave_ticket",
                            "посвідчення про відрядження": "deployment_certificate"}
  evaluate.py:567 той САМИЙ літерал-словник знову, всередині виразу template_ok
  Дві копії існують незалежно — розбіжність між ними не може бути виявлена
  нічим, і саме template_ok (окрема цифра звіту «шаблон 16/16») збрехала б.
  Суперечить шапці field-mapping.yaml:4-5 («новий шаблон... не має вимагати
  правки скрипта оцінки») — прочитано дослівно.
```

```
id: R-A1-07
verdict: confirmed (факт), severity ЗНИЖЕНО
severity_after: nit  (було should-fix)
already_known: no
how_verified: identification.py:20-26 прочитано — імпортує build_record
  (CONSISTENCY_RULES, DERIVE_FUNCS), classify, blank_form, extract
  (NAME_PART_ROLES, field_part, name_group_key), normalize (PLACEHOLDER-ключі),
  subject_kind. subject_kind.py:6-8 дійсно обґрунтовує свою окремість циклом.
note: знижую до nit, бо заміряного наслідку немає жодного: цикл не існує
  сьогодні, поведінка правильна, тести зелені. Це вартість МАЙБУТНЬОЇ зміни, а
  не дефект. У черзі виконавця має стояти після всього, що дає неправильні дані.
```

```
id: R-A1-11
verdict: confirmed
severity_after: nit
already_known: no
how_verified: grep 'first_block_matching|starts_with' по pipeline і eval:
  у pipeline/schemas/*.yaml — НУЛЬ входжень; у коді — extract.py:1419 (докстрінг),
  :1432 UNANCHORED_MODES, :1567-1573 сама гілка, identification.py:122
  EXTRACTION_REQUIRED_KEYS.
note: посилання A1 «identification.py:1432» помилкове — 1432 це extract.py
  (identification.py має 640 рядків). Суть претензії не змінюється.
```

```
id: R-A1-12
verdict: confirmed (частково — власною перевіркою підтверджено КОД, не хронометраж)
severity_after: nit
already_known: no
how_verified: pipeline/config.py:49 `self_consistency_n: 1`;
  прямий виклик majority_vote(['a']) -> ('a', False), тобто при n=1
  `llm_split_vote` недосяжний за побудовою, а він при цьому член
  build_record.UNRELIABLE_METHODS і CONFIDENCE_BY_METHOD (0.3) — два записи в
  моделі довіри, які на дефолтному конфізі не спрацьовують ніколи.
note: цифру «1,5-4 хв на виклик» я НЕ переміряв (це вимагало б прогону з
  моделлю, а знахідка його не потребує). Тому підтверджую лише перевірене:
  механізм існує, за замовчуванням мертвий, і додає два стани довіри.
  Аргумент «ніколи не буде ввімкнений» лишається аргументом команди, не
  моїм заміром.
```

```
id: R-A1-13
verdict: confirmed
severity_after: nit
already_known: no
how_verified: читання порядку викликів. process_file: `_persist(meta, text, res)`
  — run.py:622, останній рядок перед `return meta`. archived_to з'являється в
  process_target на run.py:739, тобто ПІСЛЯ повернення з process_file, і далі
  читається лише в run_pipeline.py:123-124 (консольний рядок). grep
  'archived_to' дає рівно ці три місця — у збережений .md ключ не потрапляє
  жодним шляхом.
note: прогону в directory-mode свідомо не робив: він фізично переносить файли з
  папки-приймача, а порядок викликів однозначний і без нього.
```

```
id: R-A1-14
verdict: confirmed
severity_after: nit
already_known: no
how_verified: прямі значення + читання обох джерел:
  classify.classify_domain_rules: `scores[domain] = title_hits*3 + body_hits`
  identification.TITLE_WEIGHT == 5, ANCHOR_WEIGHT == 2
  Дублювання фраз перевірив очима: domain_keyphrases.yaml, leave.title =
  ["відпускний квиток"] — той самий рядок у leave_ticket.yaml:35 title; фраза
  «після закінчення строку відпустки» стоїть І в leave.body довідника, І в
  anchors схеми (leave_ticket.yaml:39).
  Правило нічиєї реалізоване двічі (classify: best==runner_up -> None;
  identification: best_score > runner_up_score + anchor_ok).
```

```
id: R-A1-15 + R-A2-13  (ЗЛИТО — одне місце, два наслідки)
verdict: confirmed
severity_after: nit
already_known: no
how_verified: local_store.py:81-96 прочитано дослівно: `with open(path,"w")` +
  `f.write(content)`, далі ОКРЕМИЙ `open(index_path,"a")` (з блокуванням файлу
  індексу, але без зв'язку з першим записом). Ні tmp+rename, ні відкату, ні
  звірки documents/ з індексом на старті.
  Наслідок A2-13 (запис без рядка в індексі -> наступний прогін дасть другий
  запис) — той самий фінальний стан, який я вже спостерігав фізично в репро
  R-A1-04: два .md з одним file_hash.
note: власне обрив процесу посеред write я не імітував — це властивість
  файлового API, не коду, і перевіряти нічого. Обидві знахідки — про один
  рядок, тому злиті.
```

```
id: R-A2-07
verdict: confirmed
severity_after: nit
already_known: no
how_verified: grep '"термін не розпізнано"' — рівно два робочі входження:
  normalize.py:431 (продукція) і build_record.py:341 (порівняння літералом),
  плюс згадка в комментарі extract.py:902. Спільної константи немає.
  Поруч у тому самому build_record імпортуються UNVERIFIED_METHOD і
  NAME_PART_ROLES саме з мотивом «рядок мусить мати одне джерело» — тобто
  проєкт застосовує це правило вибірково. Претензія точна.
```

```
id: R-A2-08
verdict: confirmed
severity_after: nit
already_known: no
how_verified: normalize.py:429-431 — `re.sub(r"\s*за\s*\d{4}\s*рік\s*[,.;]?\s*$",
  "", ...)` стоїть УСЕРЕДИНІ match_dictionary, тобто діє на кожне category-поле
  будь-якої схеми (сьогодні leave_type і military_rank). Підтверджено й
  походження: build_record._YEAR_SUFFIX працює з тим самим суфіксом окремим
  дериватором (leave_year), тобто знання про «за NNNN рік» уже живе у ДВОХ
  місцях коду.
```

```
id: R-A2-10
verdict: confirmed
severity_after: nit
already_known: no
how_verified: evaluate.py:618-619 — `RANK_ALIASES = res["dictionaries"].get(
  "military_rank", {})`, тобто рівно той словник, який build_resources віддає
  пайплайну. Контраст із evaluate.py:165-172, де для гомогліфів свідомо
  зроблена ВЛАСНА копія з написаним обґрунтуванням. Для звань такого
  обґрунтування в коді немає — перевірив читанням усього блоку.
```

```
id: R-B1-05
verdict: confirmed
severity_after: nit
already_known: no
how_verified: копія LEAVE-001.docx під іменем 05_docx_renamed.pdf у scratch,
  process_file --no-llm:
  status=confirmed, source_kind=photo, template=leave_ticket
  warnings=['пропущено 1 сторінок без текстового шару (№ 4) -- OCR не налаштовано']
  subject={'surname':'ЛЕМЕШКО','given_name':'Соломія','patronymic':'Романівна'}
  Механізм у коді підтверджений: ingest.py:325-328 —
  `info["source_kind"] = "photo" if info.get("scan_pages_detected") else "electronic"`,
  а рендер docx через MuPDF дає сторінку без текстового шару.
note: severity nit правильна — жодного неправильного ЗНАЧЕННЯ немає, лише
  неправильний source_kind. Але це вхід до R-A1-10: якби документ був
  needs_review, він поїхав би в чергу «рукописне».
```

---

## (1) Черга виконавця — confirmed за реальною критичністю

**critical**

1. **R-A1-01 + R-A2-01** — фінальний `confirmed` не пишеться в `facts`;
   `needs_review`-документ їде в підрахунки як підтверджений факт.
   Виправляти РАЗОМ з R-A2-02, інакше регресія знову буде невидима.
2. **R-B1-02** — 4-й і подальші токени ПІБ зникають мовчки; документ
   `confirmed` з чужою ідентичністю особи.

**should-fix** (порядок = ціна тихої неправди)

3. **R-A2-02** — прилад не міряє статуси: повний розрив «чернетка ≠ факт» на
   16 документах дає ті самі 176/176.
4. **R-B1-03** — часткова сума числівника (20) з провенансом matched/0.9;
   захист одношаровий.
5. **R-B1-04** — неможлива дата гасить поле без причини й без сирого збігу
   (`raw_value` — dict, тому механізм `unresolved_values` не спрацьовує).
6. **R-A1-06 + R-A2-05** — значення `normalization:` не валідується; одруківка
   перетворює підтверджено-порожнє поле на текстове значення в БД.
7. **R-A1-02** — відсутній `blank_template` дає `recognized: True` при нулі
   доказів і мовчки вимикає три захисти.
8. **R-A2-06** — документ, що впав необробленою помилкою, не існує ніде, крім
   консолі, і лишається в папці-приймачі.
9. **R-B1-01** — тематичний домен за ОДНИМ збігом фрази, плюс
   `create_subject_object: True` на невпізнаному документі.
10. **R-A1-04** — `--reprocess` лишає два повні записи на той самий вміст.
11. **R-A2-03** — 7+7 полів схем не міряються приладом, попередження немає.
12. **R-A2-04** — `document_links` не міряються, а звіт стверджує протилежне.
13. **R-A1-10 + R-A2-12** — `handwritten` кожному фото з `needs_review`.
14. **R-A1-08** — константи родини бланків без схемного перевизначення
    (вже відоме, наслідок заміряний).
15. **R-A1-09** — інжест викидає (таблиця, рядок, стовпець) при читанні
    (вже відоме; пропозиція — найдешевший перший крок).
16. **R-A1-05 + R-A2-09** — прилад двічі хардкодить мапу «тип → template».
17. **R-A1-03 + R-A2-11** — `number` приварений до семантики днів (латентно).

**nit** (у порядку дешевизни): R-A2-07, R-A1-11, R-A1-13, R-A2-08, R-A2-10,
R-A1-15 + R-A2-13, R-B1-05, R-A1-14, R-A1-12, R-A1-07.

## (2) Метрика сліпих рецензентів

27 унікальних знахідок, усі 27 `confirmed`.
Мають відповідник у `docs/known-weak-spots.md` — **5**:

| знахідка | розділ |
|---|---|
| R-A1-01 + R-A2-01 | 8.6 (клас названий, закритий лише для `UNVERIFIED_METHOD`) |
| R-A2-02 | 2.17 (сказано, що перевірка позначки додана рівно для `swapped_dates`) |
| R-A2-03 | 5.6 + розд. 7, пункт A4 (відкритий запит до власника приладу) |
| R-A1-08 | 2.8 + 8.5 |
| R-A1-09 | 2.8 + 2.6 |

**НОВИХ (без відповідника) — 22 з 27, тобто 81%.** Серед них обидві критичні за
підсумковою оцінкою (R-B1-02 повністю нова; R-A1-01 нова саме як НЕЗАКРИТА
частина відомого класу) і 9 з 15 should-fix.

Два суміжні випадки, які я НЕ зарахував як «вже відомо», і чому:
- **R-A1-04** проти 2.9: 2.9 — про `ON CONFLICT DO NOTHING` у ЧУЖОМУ
  завантажувачі; подвоєння `.md` на нашому диску там не описане.
- **R-B1-03** проти 2.17: побічне спостереження 2.17 — про `abs()` у
  `_days_span_inclusive`; часткова сума `number_from_words` — інший механізм.
