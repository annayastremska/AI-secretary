"""Будує JSON Schema для групи полів схеми -- використовується як grammar
для LLM-виклику (llama.cpp constrained decoding), щоб LLM фізично не могла
видати невалідну структуру чи неіснуючий код категорії. Жодної залежності
від llama_cpp тут немає -- сам виклик моделі лишається в ноутбуці
(pipeline/*.py не тягне важкі ML-залежності), цей модуль лише формує
контракт (JSON Schema), який ноутбук передає в LlamaGrammar.from_json_schema.

Кожне поле навмисно nullable (через anyOf .../null) -- без цього grammar
змушує LLM видати ХОЧ ЩОСЬ навіть коли значення справді відсутнє в тексті,
що провокує галюцинації замість чесного "не знайдено".
"""


def _category_codes(field, dictionaries):
    lookup = dictionaries.get(field["category"], {})
    return sorted({code for code, _label in lookup.values()})


def _field_json_schema(field, dictionaries):
    field_type = field.get("type")

    if field_type == "category":
        codes = _category_codes(field, dictionaries)
        if not codes:
            # довідник порожній/не завантажений -- поле нічим не обмежити,
            # тож не змушуємо LLM вигадувати код із порожнього списку
            return {"type": ["string", "null"]}
        return {"anyOf": [{"type": "string", "enum": codes}, {"type": "null"}]}

    if field_type == "date":
        return {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "day": {"type": "string"},
                        "month": {"type": "string"},
                        "year": {"type": "string"},
                    },
                    "required": ["day", "month", "year"],
                },
                {"type": "null"},
            ]
        }

    if field_type == "number":
        return {"anyOf": [{"type": "integer"}, {"type": "null"}]}

    # text / object_ref за замовчуванням
    return {"type": ["string", "null"]}


def build_json_schema_for_fields(schema, dictionaries, field_names):
    """schema: schemas/*.yaml, завантажена. dictionaries: {category: alias_lookup}
    (те, що повертає build_alias_lookup). field_names: підмножина полів
    schema["fields"], для яких LLM має видати значення в цьому виклику
    (напр. одна група "прогалин" після детермінованого проходу).

    Повертає (json_schema, field_defs) -- json_schema для grammar,
    field_defs -- ті самі field-описи зі схеми, у тому ж порядку, для
    подальшого прив'язування назви поля до note/type при постобробці.
    """
    field_by_name = {f["name"]: f for f in schema["fields"]}
    field_defs = [field_by_name[name] for name in field_names if name in field_by_name]

    properties = {f["name"]: _field_json_schema(f, dictionaries) for f in field_defs}
    json_schema = {
        "type": "object",
        "properties": properties,
        "required": [f["name"] for f in field_defs],
    }
    return json_schema, field_defs


def chunk_fields(field_names, batch_size):
    """Ділить список назв полів на групи розміром batch_size -- компроміс
    між "один виклик на все" (швидко, але збій одного виклику валить усі
    поля разом) і "один виклик на поле" (ізольовано, але повільно на CPU).
    Останню групу теж повертає, навіть якщо вона менша за batch_size."""
    return [field_names[i:i + batch_size] for i in range(0, len(field_names), batch_size)]
