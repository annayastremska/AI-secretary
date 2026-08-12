<!--
Це РЕАЛЬНИЙ вивід пайплайна, не вигаданий вручну: згенеровано прогоном
`process_file` на синтетичному зразку `data/samples/leave/synthetic-2026-05/docx/LEAVE-001.docx`
(12.08.2026, з увімкненою LLM). Це саме те, що піде в БД-споживача через
контракт, описаний у `db-handoff-notes.md` -- дивіться той файл для
пояснення кожного ключа й мапінгу на таблиці БД.

Єдина ручна правка нижче -- `storage_key`: генерація йшла без реального
сховища (`res["store"] = None`, щоб не писати в data/output), тому це поле
не заповнилось саме процедурою; значення дописано за задокументованою
формулою `LocalDocumentStore.key_for` (`documents/<domain>/<id>.md`) з тими
самими реальними `domain`/`id`, що й у решті запису -- не вигадка, а
детермінований розрахунок від тих-таки даних.

Цей документ обрано навмисно: категорія "правильний" (не зіпсований
навмисно), статус `confirmed`, домен `leave`. Приклад із домену `deployment`
(`deployment_certificate`) відрізняється лише набором полів схеми -- сама
форма запису (YAML-шапка + розпізнаний текст) та сама.
-->

---
id: e4db209b-2d5d-41b8-abd2-02291dd7e367
status: confirmed
file_hash: ae31f4de5dfb754f6e312eb94c77d87744e1afeb96fb8ab190920ba99f0ff524
source_file: LEAVE-001.docx
source_kind: electronic
uploaded_at: '2026-08-12T17:31:19.797975+00:00'
domain: leave
template: leave_ticket
identification:
  source: anchors
  score: 15
  runner_up: 0
storage_key: documents/leave/e4db209b-2d5d-41b8-abd2-02291dd7e367.md
reason: null
review_reason: null
review_queue: null
subject:
  rank:
    code: soldier
    label: Солдат
  surname: ЛЕМЕШКО
  given_name: Соломія
  patronymic: Романівна
  person_alias: ЛЕМЕШКО Соломія Романівна
  person_complete: true
facts:
- fact_type: leave
  value_code: щорічна основна відпустка за 2026 рік
  date_start: '2026-05-10'
  date_end: '2026-05-22'
  confirmed: true
  confidence: 0.9
  status: current
  superseded_by_document_id: null
  additional_info:
    destination_place: м. Житомир
    leave_year: 2026
    duration_days: 13
    actual_return_date: '2026-05-23'
    document_number: '102'
    document_date: '2026-05-09'
    travel_document_number: 8144/26
    unit_to_report: військова частина А0000
  source_document_id: e4db209b-2d5d-41b8-abd2-02291dd7e367
- fact_type: leave_place
  value_code: м. Житомир
  date_start: null
  date_end: null
  confirmed: true
  confidence: 0.9
  status: current
  superseded_by_document_id: null
  additional_info: {}
  source_field: destination_place
  source_document_id: e4db209b-2d5d-41b8-abd2-02291dd7e367
- fact_type: leave_days
  value_code: '13'
  date_start: null
  date_end: null
  confirmed: true
  confidence: 0.9
  status: current
  superseded_by_document_id: null
  additional_info: {}
  source_field: duration_days
  source_document_id: e4db209b-2d5d-41b8-abd2-02291dd7e367
- fact_type: leave_actual_return
  value_code: '2026-05-23'
  date_start: null
  date_end: null
  confirmed: true
  confidence: 0.9
  status: current
  superseded_by_document_id: null
  additional_info: {}
  source_field: actual_return_date
  source_document_id: e4db209b-2d5d-41b8-abd2-02291dd7e367
- fact_type: document_number
  value_code: '102'
  date_start: null
  date_end: null
  confirmed: true
  confidence: 0.9
  status: current
  superseded_by_document_id: null
  additional_info: {}
  source_field: document_number
  source_document_id: e4db209b-2d5d-41b8-abd2-02291dd7e367
- fact_type: document_date
  value_code: '2026-05-09'
  date_start: null
  date_end: null
  confirmed: true
  confidence: 0.9
  status: current
  superseded_by_document_id: null
  additional_info: {}
  source_field: document_date
  source_document_id: e4db209b-2d5d-41b8-abd2-02291dd7e367
- fact_type: unit_to_report
  value_code: військова частина А0000
  date_start: null
  date_end: null
  confirmed: true
  confidence: 0.9
  status: current
  superseded_by_document_id: null
  additional_info: {}
  source_field: unit_to_report
  source_document_id: e4db209b-2d5d-41b8-abd2-02291dd7e367
field_provenance:
  rank:
    method: matched
    criticality: critical
    resolved: true
    confidence: 0.9
  surname:
    method: matched
    criticality: critical
    resolved: true
    morphology: already_nominative
    confidence: 0.9
  given_name:
    method: matched
    criticality: critical
    resolved: true
    morphology: already_nominative
    confidence: 0.9
  patronymic:
    method: matched
    criticality: critical
    resolved: true
    morphology: already_nominative
    confidence: 0.9
  leave_type_and_destination:
    method: matched
    criticality: critical
    resolved: true
    confidence: 0.9
  destination_place:
    method: matched
    criticality: optional
    resolved: true
    confidence: 0.9
  leave_year:
    method: derived
    criticality: optional
    resolved: true
    confidence: 0.8
  duration_days:
    method: matched
    criticality: optional
    resolved: true
    confidence: 0.9
  leave_start_date:
    method: matched
    criticality: critical
    resolved: true
    confidence: 0.9
  leave_end_date_planned:
    method: matched
    criticality: critical
    resolved: true
    confidence: 0.9
  actual_return_date:
    method: matched
    criticality: optional
    resolved: true
    confidence: 0.9
  document_number:
    method: matched
    criticality: optional
    resolved: true
    confidence: 0.9
  document_date:
    method: matched
    criticality: optional
    resolved: true
    confidence: 0.9
  travel_document_number:
    method: matched
    criticality: optional
    resolved: true
    confidence: 0.9
  unit_to_report:
    method: matched
    criticality: optional
    resolved: true
    confidence: 0.9
  co_travelers:
    method: deferred
    criticality: optional
    resolved: false
  authorizing_commander:
    method: deferred
    criticality: optional
    resolved: false
unknown_fields:
- co_travelers
- authorizing_commander
unknown_critical_fields: []
confirmed_empty_fields: []
not_implemented_fields:
- co_travelers
- authorizing_commander
date_range_error: null
unresolved_values: {}
warnings: []
---

## Розпізнаний текст

Додаток 30
до Інструкції з діловодства у Збройних Силах України
(підпункт 2.8.9)
Кутовий штамп
військової частини (установи)
військова частина А0000
Відпускний квиток
№ 102    від 09.05.2026
Дійсний у разі пред’явлення документа, який засвідчує особу.
Командир (начальник)
підполковник                                        Андрій ЛИТВИНЕНКО
(військове звання підпис Власне ім’я  ПРІЗВИЩЕ)
М.П.
2
Продовження додатка 30
Зворотний бік відпускного квитка
Командир (начальник)
підполковник                                        Андрій ЛИТВИНЕНКО
(військове звання підпис Власне ім’я  ПРІЗВИЩЕ)
М.П.
Відмітка про постановку на облік та зняття з обліку
рядовий ЛЕМЕШКО Соломія Романівна
(військове звання, прізвище, ім’я та по батькові)
звільнена
щорічна основна відпустка за 2026 рік
(вид відпустки та найменування населеного пункту,
м. Житомир
до якого звільнено військовослужбовця)
терміном на
тринадцять
(кількість днів прописом)
з “10” травня 2026 р.  по “22” травня 2026 р.
Після закінчення строку відпустки
рядовий ЛЕМЕШКО С.Р.
(військове звання, прізвище та ініціали
зобов’язана прибути до місця служби у
військова частина А0000
(найменування військової частини або населеного пункту)
“23” травня 2026 р.
(дата повернення)
Для проїзду видано військові перевізні документи за №
8144/26
Разом з
чоловік Лемешко А., діти — 1
(військове звання, прізвище та ініціали
прямують
