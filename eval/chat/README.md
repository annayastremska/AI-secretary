# eval/chat — розмічені набори для чата

Кожен файл має шапку з описом формату — читати її, а не вгадувати за назвою.
Траси наступних ходів і три `query_catalog_v*.yaml` — Андрієві, приїхали з
main 01.09.2026; `identity.tsv` наш і лежав тут раніше.

| Файл | Що це | Хто читає |
|---|---|---|
| `followups.tsv` | траси наступних ходів: чи розв'язує модель посилання на попередню репліку | `measure_followup_route.py`, `measure_rewrite_followup.py`, `measure_date_naming.py`, `measure_catalog_variants.py` |
| `followups_held_out.tsv` | відкладена частина тих самих трас (на ній не налаштовуються) | `measure_rewrite_followup.py` |
| `followups_v2.tsv` | друга редакція трас | `measure_date_naming.py` |
| `identity.tsv` | **наш**: 15 питань про ідентифікатор документа | `demos/upload_app/measure_identity.py`, `demos/upload_app/tests/test_identity.py` |

## Три `query_catalog_v*.yaml` — це НЕ каталог

`query_catalog_v1.yaml`, `query_catalog_v2.yaml`, `query_catalog_v1v2.yaml` —
**заморожені входи замірів** (порівняння варіантів назв дат і формулювань
шаблонів). Вони застарілі навмисно: 1114 рядків проти 1379 у живому каталозі,
і наших нових шаблонів (`list_by_place`, `list_place_within_state`) у них
немає.

**Живий каталог один: `demos/upload_app/query_catalog.yaml`.** Правити тут —
означає правити не те, що працює, і при цьому зіпсувати замір, з яким
порівнюють. Якщо потрібен новий замір — новий файл із новою назвою, а не
редагування старого.
