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

    def _messages(self, user_content):
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_content})
        return messages

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

    def extract_batch(self, field_defs: list, context_text: str, json_schema: dict) -> dict:
        """Один виклик на групу полів; повертає {ім'я поля: значення}.
        json_schema робить кожне поле nullable -- тому в промпті прямо
        сказано повертати null: без цього модель під grammar змушена була б
        вигадати значення там, де його в тексті немає."""
        field_descriptions = "\n".join(
            f"- {f['name']}" + (f" ({f['note'].strip()})" if f.get("note") else "")
            for f in field_defs
        )
        user = (
            "З наведеного тексту документа витягни значення полів нижче. "
            "Якщо значення поля в тексті немає -- поверни null для цього поля, "
            "не вигадуй. Відповідай лише JSON-об'єктом.\n\n"
            f"Поля:\n{field_descriptions}\n\nТекст документа:\n{self._trim(context_text)}"
        )
        grammar = self._grammar_from_json_schema(json_schema)
        with self._lock:
            resp = self.llm.create_chat_completion(
                messages=self._messages(user),
                max_tokens=64 * max(1, len(field_defs)),
                temperature=self.temperature, grammar=grammar,
            )
        return json.loads(resp["choices"][0]["message"]["content"])


def load_system_prompt(path) -> str:
    if not path or not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()
