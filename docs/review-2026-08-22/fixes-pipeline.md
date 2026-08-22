# Виправлення в пайплайні за рев'ю 22.08.2026

Вхід: `verdicts.md` (адверсарно верифіковані знахідки). Ділянка — `pipeline/**`
і `docs/known-weak-spots.md`. `eval/**` виправляє інший виконавець паралельно.

## Як міряється регресія

Паралельний виконавець змінює `eval/evaluate.py` ПО ХОДУ (додає перевірки за
A-01/A-09), тому «224/224» на робочому дереві вже не відтворюється: прилад
станом на цю сесію дає **240 перевірок**. Щоб цифра лишалась порівнюваною,
базова лінія міряється приладом З HEAD `2e344ba` (копія `evaluate.py` у
scratchpad, `PYTHONPATH=<scratchpad> python -m eval_head.evaluate`), а не
робочою копією:

```
python -m pytest eval/tests -q                                        -> 226 passed
eval_head.evaluate --no-llm --input .../leave/synthetic-2026-05/docx  -> 224/224 (100.0%)
eval_head.evaluate --no-llm --input .../deployment/.../docx           -> 183/183 (100.0%)
нормативний корпус (42 файли, process_file)                           -> 41 confirmed / 1 unresolved
```

Робочий прилад (з правками паралельного виконавця) на тих самих даних дає
240/240 (100.0%) і 14/14 шаблонів — тобто відсоток тримається в обох мірках.

## Зроблено

### C-02 — `supersession_note` давав `supersedes` зі згадки в тексті [ЗАКРИТО]

Файли: `pipeline/schemas/leave_ticket.yaml`,
`pipeline/schemas/deployment_certificate.yaml`.

**Репро ДО** (LEAVE-001 + один дописаний абзац, повний ланцюг через
`process_file`):

```
'ВПД анульовано, видано нові.'                       confirmed=True  supersedes evidence='анульовано'
'Порядок анульовання квитків визначено інструкцією.' confirmed=True  supersedes evidence='анульовання'
'Відпустка перервана достроково не була.'            confirmed=True  supersedes evidence='перервана'
```

**Фікс:** позначка мусить стояти в ДУЖКАХ і бути окремим словом
(`\((?P<value>[^()]{0,120}?\b(?:…)\b[^()]{0,120}?)\)`). Дужки — не
косметика: на всіх трьох парах еталона позначка надрукована саме в дужках, а
згадка в суцільному тексті — ні. Значенням стає весь вміст дужок, тобто
рев'юер бачить позначку так, як вона надрукована.

**Репро ПІСЛЯ:** усі три штучні випадки — `document_links = []`; справжні пари
лишились:

```
LEAVE-014 supersession_note='перервана, відкликаний з відпустки'         № None
LEAVE-016 supersedes_document_number='157' + note='виданий замість анульованого квитка № 157'
TRIP-014  supersedes_document_number='254' + note='переоформлено замість посвідчення № 254'
```

Лишається відкритим (звужено, не закрито): згадка В ДУЖКАХ усередині
суцільного тексту правило пройде.

### C-03 — реквізити чужого наказу як підтверджені факти [ЗАКРИТО]

Файли: `pipeline/extraction/extract.py` (`extract_field_regex`,
`AMBIGUOUS_MATCH_METHOD`), `pipeline/build_record.py` (raw_text для рев'юера).

**Репро ДО** (LEAVE-001 + «Відповідно до наказу командира № 777/К від
01.01.2020 р.»): `document_number = 777/К` (правильно 102),
`document_date = 2020-01-01` (правильно 2026-05-09), обидва
`method=matched, resolved=True, confidence=0.9`, і обидва доїжджають у
`facts` ОКРЕМИМИ рядками з `confirmed=true`.

**Фікс:** `pattern.search` → `finditer`; більше одного РІЗНОГО збігу одного
варіанта → `None, "ambiguous_multiple_matches:<кандидати>"`. Однакові збіги
неоднозначністю не є. Правило загальне для всіх regex-полів, а не для двох:
«бери перший збіг у документі» ніде не було обґрунтоване, воно дісталось від
`search()`.

**Репро ПІСЛЯ:**

```
document_number  method='ambiguous_multiple_matches:777/К, 102'      resolved=False  raw_text='777/К, 102'
document_date    method='ambiguous_multiple_matches:01 01 2020, 09 05 2026'  resolved=False
facts із source_field document_number/document_date: []
базовий LEAVE-001: номер=102, дата=2026-05-09, confirmed  (без змін)
```

**Регресія:** 226 passed; 224/224; 183/183; нормативний корпус 41/1.
