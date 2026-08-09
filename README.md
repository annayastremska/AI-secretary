# AI-секретар

Чатбот для зведених запитів по документообігу організації. Технічне
завдання: [`context/ТЗ_AI-секретар.docx`](context/ТЗ_AI-секретар.docx).

Ця частина роботи — **пайплайн обробки документів**: файл на вході,
структурований запис на виході. Зовнішнє сховище (MinIO) і база даних — поза
її межами.

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
- `data/output/` — витягнуті записи: `documents/<domain>/<id>.md` з YAML-шапкою
  (`subject`, `facts`, провенанс кожного поля); `unresolved` — окремою текою.
- `dictionaries/` — довідники допустимих значень категоріальних полів.
- `schemas/` — схема полів на кожен тип бланка (regex/LLM/дата/category/
  object_ref) + блок `identification`, за яким пайплайн сам упізнає бланк.
- `pipeline/` — інжест → ідентифікація шаблону → екстракція → нормалізація
  → збірка запису → локальне сховище.
- `scripts/download_model.py` — завантаження ваг моделі (pip цього не вміє).
- `notebooks/` — експерименти з моделями. Colab-ноутбук пайплайна застарів,
  канонічний шлях — `run_pipeline.py`.

## Встановлення

Повний запуск (`.docx` / `.pdf` + LLM) — три команди:

```bash
pip install -r requirements.txt
python scripts/download_model.py        # ваги MamayLM 12B (~6.8 ГБ) у models/
cp config.example.yaml config.yaml
```

`config.example.yaml` вказує саме туди, куди скрипт кладе ваги, тому конфіг
правити не потрібно. Ваги окремим кроком, бо pip їх встановити не може: це
файл на кілька ГБ, а не пакет. Якщо машина не тягне 12B, є
`python scripts/download_model.py --size 4b` (~2.5 ГБ) і одна правка
`model_path` у `config.yaml`.

**Про `llama-cpp-python`:** на PyPI лежить лише source distribution, тому
звичайний `pip install` компілював би його з джерел і вимагав C++-тулчейн
(на Windows — MSVC Build Tools). `requirements.txt` тягне готові CPU-wheel-и
з офіційного індексу проєкту, тож компілятор не потрібен. Для GPU — замініть
`/cpu` на свій бекенд у `--extra-index-url` і поставте `n_gpu_layers: -1`.

**Якщо ваг ще немає** — прогін не падає: буде попередження, а поля
`extraction: llm` лишаться чесно позначеними прогалинами (`needs_review`
замість `confirmed`). Без LLM повністю обходяться лише два пакети,
`pyyaml` + `python-docx`.

Для фото й сканованих PDF додатково потрібен `surya-ocr` (тягне torch,
~1.5 ГБ власних ваг) — див. [`requirements-optional.txt`](requirements-optional.txt).
Для `.docx` і PDF з текстовим шаром він не потрібен.

Розроблено й перевірено на Python 3.13; синтаксис не використовує нічого
новішого за 3.8, але на старіших версіях не тестувалось.

## Запуск

Прогін на демонстраційному документі (посвідчення про відрядження) —
результат буде в `data/output/documents/deployment/`:

```bash
python run_pipeline.py --input data/samples/deployment/посвідчення_відрядження_заповнений.docx
```

Файл, переданий через `--input`, **не** переміщується, тому цю команду можна
повторювати (повторний прогін того самого вмісту дасть `duplicate` — це
дедуплікація за хешем, а не помилка; щоб прогнати заново, очистіть
`data/output`).

Решта режимів:

```bash
python run_pipeline.py                     # обробити все з data/inbox (і перенести оброблене)
python run_pipeline.py --no-llm            # лише детермінований прохід
python run_pipeline.py --template leave_ticket   # примусова схема
python run_pipeline.py --dry-run           # нічого не зберігати
```

`config.yaml` у `.gitignore` — це налаштування конкретної машини; у
репозиторії лежить лише [`config.example.yaml`](config.example.yaml).

Прогін **без моделі** — неповний режим: поля `extraction: llm` лишаються
порожніми, тому запис виходить `needs_review`, а не `confirmed`. Флаг
`--no-llm` потрібен лише щоб вимкнути LLM для окремого прогону, коли в
конфізі вона ввімкнена. LLM і OCR підключаються окремо й незалежно одне
від одного.

Деталі, інваріанти й відомі обмеження —
[`context/extraction-pipeline-prototype.md`](context/extraction-pipeline-prototype.md).
