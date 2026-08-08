# AI-секретар

Чатбот для зведених запитів по документообігу організації. Технічне
завдання: [`context/ТЗ_AI-секретар.docx`](context/ТЗ_AI-секретар.docx).

## Структура

- `context/` — ТЗ, план і нотатки з обговорення вимог та архітектури.
- `data/samples/<domain>/` — приклади документів по доменах (leave,
  deployment, equipment, staffing), для розробки й тестування пайплайна.
- `data/inbox/` — папка-приймач: `python run_pipeline.py` без аргументів
  обробляє все, що лежить безпосередньо в ній (без підпапок), і переносить
  оброблене далі, щоб приймач спорожнявся.
- `data/processed/<дата>/`, `data/failed/<дата>/` — куди переносяться
  оброблені файли; `failed` — це `unresolved`, тобто ті, чий шаблон не
  впізнано й на які має глянути людина.
- `data/output/` — витягнуті записи (та сама схема ключів, що планується в
  MinIO: `documents/<domain>/<id>.md`; `unresolved` — завжди окремою текою).
- `dictionaries/` — довідники допустимих значень категоріальних полів.
- `schemas/` — схема полів на кожен тип бланка (regex/LLM/дата/category/
  object_ref) + блок `identification`, за яким пайплайн сам упізнає бланк.
- `pipeline/` — інжест → ідентифікація шаблону → екстракція → нормалізація
  → збірка запису; шар сховища (локально / MinIO) і запис у Postgres.
- `db/schema.sql` — DDL під трьохшарову структуру (documents / subjects /
  facts / review_queue).
- `notebooks/` — експерименти з моделями. Colab-ноутбук пайплайна застарів,
  канонічний шлях — `run_pipeline.py`.

## Встановлення

Повний запуск (`.docx` / `.pdf` + LLM) — три команди:

```bash
pip install -r requirements.txt
python scripts/download_model.py        # ваги MamayLM 4B (~2.5 ГБ) у models/
cp config.example.yaml config.yaml
python run_pipeline.py
```

У `data/inbox/` уже лежить демонстраційний документ, тому остання команда
одразу має що обробити, і `config.example.yaml` вказує саме туди, куди скрипт
кладе ваги — конфіг правити не потрібно.

Ваги окремим кроком, бо pip їх встановити не може: це файл на кілька ГБ, а не
пакет. Для якіснішої 12B — `python scripts/download_model.py --size 12b`
(~6.8 ГБ) і одна правка `model_path` у `config.yaml`.

**Про `llama-cpp-python`:** на PyPI лежить лише source distribution, тому
звичайний `pip install` компілював би його з джерел і вимагав C++-тулчейн
(на Windows — MSVC Build Tools). `requirements.txt` тягне готові CPU-wheel-и
з офіційного індексу проєкту, тож компілятор не потрібен. Для GPU — замініть
`/cpu` на свій бекенд у `--extra-index-url` і поставте `n_gpu_layers: -1`.

**Якщо ваг ще немає** — прогін не падає: буде попередження, а поля
`extraction: llm` лишаться чесно позначеними прогалинами (`needs_review`
замість `confirmed`). Без LLM повністю обходяться лише два пакети,
`pyyaml` + `python-docx`.

Окремо, під конкретні сценарії (див.
[`requirements-optional.txt`](requirements-optional.txt)):

| навіщо | що ставити | розмір |
|---|---|---|
| фото й скановані PDF | `surya-ocr` (тягне torch) | ~1.5 ГБ ваг |
| MinIO замість файлів | `minio` | — |
| Postgres | `psycopg[binary]` | — |

Розроблено й перевірено на Python 3.13; синтаксис не використовує нічого
новішого за 3.8, але на старіших версіях не тестувалось.

## Запуск

```bash
python run_pipeline.py                    # обробити все з data/inbox
python run_pipeline.py --input <файл>      # один документ
python run_pipeline.py --no-llm            # лише детермінований прохід
python run_pipeline.py --dry-run           # нічого не зберігати
```

`config.yaml` у `.gitignore` — саме там з'являться DSN Postgres і ключі
MinIO; у репозиторії лежить тільки [`config.example.yaml`](config.example.yaml).

Прогін **без моделі** — це неповний режим: поля `extraction: llm` лишаються
порожніми, тому запис виходить `needs_review`, а не `confirmed`. Щоб увімкнути
повний режим, див. `llm:` у `config.example.yaml`. Флаг `--no-llm` потрібен
лише щоб вимкнути LLM для окремого прогону, коли в конфізі вона ввімкнена.
LLM і OCR підключаються окремо й незалежно одне від одного.

Деталі, інваріанти й відомі обмеження —
[`context/extraction-pipeline-prototype.md`](context/extraction-pipeline-prototype.md).

Модельні ваги (`.gguf`) зберігаються поза репозиторієм — див.
`.gitignore` і [`requirements-optional.txt`](requirements-optional.txt).
