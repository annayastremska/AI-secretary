"""Обгортка над llama-cpp-python. Уся робота з моделлю зібрана тут, а не
розмазана по клітинках ноутбука, щоб той самий код працював і локально, і
на сервері клієнта.

llama_cpp імпортується ЛІНИВО (усередині методів): решта пайплайна має
працювати на машині без встановленої моделі -- детермінований прохід дає
результат і без LLM, а поля-прогалини лишаються чесно позначеними.

Обидва режими -- grammar-constrained: модель фізично не може вийти за
межі дозволеного (enum кодів довідника / JSON за схемою), тому зникає
цілий клас помилок "невалідна структура" й "вигаданий код категорії".
Grammar НЕ гарантує правдивості значення -- лише його форму.
"""
import json
import os
import threading


def _escape_gbnf(literal: str) -> str:
    return literal.replace("\\", "\\\\").replace('"', '\\"')


class LlamaClient:
    def __init__(self, model_path, n_ctx=4096, n_gpu_layers=0, n_threads=None,
                 chat_format="gemma", system_prompt="", max_context_chars=6000,
                 temperature=0.0, verbose=False):
        if not model_path or not os.path.exists(model_path):
            raise FileNotFoundError(f"Ваги моделі не знайдено: {model_path}")
        self.model_path = model_path
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self.n_threads = n_threads
        self.chat_format = chat_format
        self.system_prompt = system_prompt or ""
        self.max_context_chars = max_context_chars
        self.temperature = temperature
        self.verbose = verbose
        self._llm = None
        self._grammar_cache = {}
        # Ліниве створення моделі й мутація кешу грамматик не потокобезпечні
        # самі по собі, а run.py прямо каже, що цей код планується викликати
        # "у майбутньому з веб-бекенда" -- тобто конкурентно. Лок дешевий і
        # знімає ризик до того, як він з'явиться. Сам інференс llama.cpp теж
        # серіалізується цим локом: один процес -> одна модель у пам'яті.
        self._lock = threading.RLock()

    # --- модель ---

    @property
    def llm(self):
        with self._lock:
            if self._llm is None:
                # CUDA-рантайм із venv -- ДО імпорту llama_cpp. На
                # GPU-сервері без цього імпорт падає цілком
                # (libcudart.so.12 не знаходиться), тобто моделі немає
                # взагалі, а не «є, але на процесорі». Розбір -- у
                # pipeline/llm/cuda_preload.py.
                from pipeline.llm.cuda_preload import preload
                preload()
                from llama_cpp import Llama
                kwargs = dict(model_path=self.model_path, n_ctx=self.n_ctx,
                              n_gpu_layers=self.n_gpu_layers, chat_format=self.chat_format,
                              verbose=self.verbose)
                if self.n_threads:
                    kwargs["n_threads"] = self.n_threads
                self._llm = Llama(**kwargs)
            return self._llm

    def _grammar_from_choices(self, choices):
        key = ("choices", tuple(choices))
        with self._lock:
            if key not in self._grammar_cache:
                from llama_cpp import LlamaGrammar
                rule = " | ".join(f'"{_escape_gbnf(c)}"' for c in choices)
                self._grammar_cache[key] = LlamaGrammar.from_string(f"root ::= {rule}")
            return self._grammar_cache[key]

    def _grammar_from_json_schema(self, json_schema):
        payload = json.dumps(json_schema, sort_keys=True, ensure_ascii=False)
        with self._lock:
            if payload not in self._grammar_cache:
                from llama_cpp import LlamaGrammar
                self._grammar_cache[payload] = LlamaGrammar.from_json_schema(payload)
            return self._grammar_cache[payload]

    # --- допоміжне ---

    def _trim(self, text: str) -> str:
        """На CPU довжина ПРОМПТУ, не відповіді, визначає час обробки. Якщо
        текст не влазить -- лишаємо початок і кінець: у бланках шапка/ПІБ
        зверху, а дати, номер наказу й підписи -- знизу, тож обрізання лише
        хвоста втратило б саме те, що шукаємо."""
        if not text or len(text) <= self.max_context_chars:
            return text
        head = int(self.max_context_chars * 0.6)
        tail = self.max_context_chars - head
        return text[:head] + "\n[...пропущено...]\n" + text[-tail:]

    def _with_guidelines(self, body: str) -> str:
        """Gemma-3 НЕ має ролі `system` як окремого ходу розмови.

        Джерело -- офіційний chat-template, вшитий у самі ваги
        (`tokenizer.chat_template` у GGUF MamayLM-Gemma-3-12B): якщо
        `messages[0]['role'] == 'system'`, шаблон бере його вміст і
        приклеює на ПОЧАТОК першого user-ходу
        (`first_user_prefix = messages[0]['content'] + '\\n\\n'`);
        тега `<start_of_turn>system` не існує взагалі.

        Раніше ми клали окреме повідомлення з `role="system"`, а
        `chat_format="gemma"` (`llama_chat_format.py:1439`) його МОВЧКИ
        викидав: його `_roles` містить лише user/assistant, і далі йде
        дослівно `_format_no_colon_single(system_message="", ...)`. Тобто
        575 токенів інструкції фізично не доходили до моделі.

        Тому склеюємо самі -- результат байт-у-байт збігається з тим, що
        дав би офіційний шаблон моделі (перевірено зіставленням двох
        форматерів), і не залежить від того, який `chat_format` увімкнено:
        повідомлення з роллю `system` більше не створюється взагалі, тож
        жоден форматер не має що втрачати чи дублювати.
        """
        if not self.system_prompt:
            return body
        return self.system_prompt.strip() + "\n\n" + body

    def _messages(self, user_content):
        return [{"role": "user", "content": self._with_guidelines(user_content)}]

    # --- публічний API, який очікує решта пайплайна ---

    def choose(self, prompt: str, choices: list) -> str:
        """Вибір рівно одного варіанта з choices (класифікація шаблону/домену)."""
        grammar = self._grammar_from_choices(choices)
        with self._lock:
            resp = self.llm.create_chat_completion(
                messages=self._messages(self._trim(prompt)),
                max_tokens=16, temperature=0.0, grammar=grammar,
            )
        return resp["choices"][0]["message"]["content"].strip()

    # Бюджет токенів НА ПОЛЕ за типом, не єдиний спільний на всю групу.
    # Вільнотекстове поле (напр. "мета відрядження") потребує на порядок
    # більше токенів, ніж код категорії чи дата -- спільний бюджет
    # (`64 * len(field_defs)`, попередня версія) означає, що ОДНЕ довге поле
    # в групі могло вичерпати ліміт до завершення JSON решти полів:
    # задокументований, не гіпотетичний канал обрізання/помилки JSON
    # (research-round-2026-08-12.md, розд. "передчасне обрізання").
    _TOKENS_PER_FIELD = {"text": 96, "object_ref": 96, "date": 32, "number": 16, "category": 16}
    _TOKENS_OVERHEAD_PER_FIELD = 16   # дужки/кома/лапки/ім'я ключа в JSON

    def _extract_max_tokens(self, field_defs) -> int:
        return sum(self._TOKENS_PER_FIELD.get(f.get("type"), 64) + self._TOKENS_OVERHEAD_PER_FIELD
                   for f in field_defs)

    def extract_batch(self, field_defs: list, context_text: str, json_schema: dict) -> dict:
        """Один виклик на групу полів; повертає {ім'я поля: значення}.
        json_schema робить кожне поле nullable -- тому в промпті прямо
        сказано повертати null: без цього модель під grammar змушена була б
        вигадати значення там, де його в тексті немає."""
        # label_before -- реальна українська фраза лейбла з бланка (напр.
        # "дата повернення") -- і note, якщо є, обидва йдуть в опис поля.
        # Раніше LLM бачила ЛИШЕ внутрішнє (латинське) ім'я поля з YAML
        # (напр. "unit_to_report") без жодного орієнтира в тексті бланка --
        # виміряний провал (docs/research/2026-08-12_ocr-geometry-speed/research-round-2026-08-12.md): полю без note
        # LLM не мала за що зачепитися серед кількох схожих сусідніх значень
        # (три дати поруч -- яка з них "дата повернення", а не "початок"?).
        #
        # _category_glossary -- "код=укр.термін" для КОЖНОГО коду enum-у
        # (schema_grammar.py). Без нього LLM бачила в grammar лише
        # латинські коди (`soldier`, `lieutenant_colonel`) без жодного
        # зв'язку з українським терміном із документа -- виміряний провал:
        # модель повернула `captain`, хоча в тексті це звання не
        # згадувалось УЗАГАЛІ. Мітка йде в ТЕКСТ інструкції, не в сам enum:
        # офіційна порада Google (Gemini structured output) -- коротші
        # назви значень enum, не довші (docs/research/2026-08-12_ocr-geometry-speed/research-round-2026-08-12.md).
        field_descriptions = "\n".join(
            f"- {f['name']}"
            + (f" (лейбл на бланку: «{f['label_before'].strip()}»)" if f.get("label_before") else "")
            + (f" ({f['note'].strip()})" if f.get("note") else "")
            + (f" [коди: {f['_category_glossary']}]" if f.get("_category_glossary") else "")
            for f in field_defs
        )
        has_category = any(f.get("_category_glossary") for f in field_defs)
        category_rule = (
            " Для полів із переліком кодів [коди: ...] -- обирай код лише "
            "якщо відповідний ЗАГАЛЬНИЙ ТЕРМІН справді присутній у тексті "
            "(у будь-якій граматичній формі); якщо жоден варіант явно не "
            "підходить -- null, а не найближчий за здогадкою."
            if has_category else ""
        )
        # ПОРЯДОК ЧАСТИН ПРОМПТУ -- СТАЛЕ СПОЧАТКУ, ЗМІННЕ В КІНЦІ.
        #
        # llama-cpp-python 0.3.34 повторно використовує KV-кеш за спільним
        # префіксом з попереднім викликом (`llama.py:909-950`, зіставлення
        # `self._input_ids` з новими токенами до першої розбіжності). Тобто
        # економить рівно стільки, скільки збігається З ПОЧАТКУ.
        #
        # Було: інструкція -> ОПИСИ ПОЛІВ -> текст документа. Описи полів
        # різні для кожної групи полів, тому вже на них префікс ламався, і
        # документ (556-570 токенів, медіана) переприфілювався ПОВНІСТЮ на
        # кожен виклик; спільного префікса лишалось ~59 токенів
        # (research-round-2026-08-13.md, розд. 4.3).
        #
        # Стало: guidelines (однакові завжди, додаються в `_messages`) ->
        # текст документа (однаковий для всіх груп полів одного документа)
        # -> інструкція+поля (єдине, що змінюється між викликами). Жоден
        # байт контексту не втрачено -- лише переставлено.
        user = (
            f"Текст документа:\n{self._trim(context_text)}\n\n"
            "З наведеного тексту документа вище витягни значення полів нижче. "
            "Якщо значення поля в тексті немає -- поверни null для цього поля, "
            f"не вигадуй.{category_rule} Відповідай лише JSON-об'єктом.\n\n"
            f"Поля:\n{field_descriptions}"
            "\n\nНагадування: null для полів, значення яких немає в тексті вище."
        )
        grammar = self._grammar_from_json_schema(json_schema)
        with self._lock:
            resp = self.llm.create_chat_completion(
                messages=self._messages(user),
                max_tokens=self._extract_max_tokens(field_defs),
                temperature=self.temperature, grammar=grammar,
            )
        return json.loads(resp["choices"][0]["message"]["content"])


def load_system_prompt(path) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()
