"""Конфіг локального запуску. Один YAML-файл замість констант, розкиданих
по клітинках ноутбука -- щоб перенесення на іншу машину було зміною конфігу,
а не переписуванням коду.
"""
import os
from copy import deepcopy

import yaml

DEFAULTS = {
    "paths": {
        "input_dir": "data/inbox",
        "output_dir": "data/output",
        "schemas_dir": "schemas",
        "dictionaries_dir": "dictionaries",
        "llm_context": "pipeline/llm_context/document_processing_guidelines.md",
    },
    "intake": {
        # Переносити оброблені файли з input_dir, щоб папка-приймач справді
        # спорожнялась, а не перечитувалась цілком на кожному запуску.
        # Діє ЛИШЕ в режимі сканування каталогу: файл, переданий явно через
        # --input, нікуди не переміщується (інакше прогін на data/samples/
        # виносив би зразки з репозиторію).
        "archive": True,
        "processed_dir": "data/processed",
        "failed_dir": "data/failed",
    },
    "llm": {
        # Вимкнений за замовчуванням: пайплайн має давати результат і без
        # моделі (детермінований прохід + чесні "прогалини"), щоб його можна
        # було прогнати на ноуті без 3-7 ГБ ваг.
        "enabled": False,
        "model_path": None,
        "n_ctx": 4096,
        # 0 = чистий CPU (ноут без CUDA); -1 = віддати всі шари GPU.
        "n_gpu_layers": 0,
        "n_threads": None,
        "chat_format": "gemma",
        "batch_size": 4,
        "self_consistency_n": 1,
        # Обрізання контексту: на CPU саме довжина промпту -- головна стаття
        # витрат, а не довжина відповіді.
        "max_context_chars": 6000,
        "verbose": False,
    },
    "ocr": {
        # surya | none. "none" -> зображення не обробляються (docx працює).
        "engine": "none",
        # Surya всередині запускає llama.cpp-сервер; на Windows зручніше
        # вказати шлях до вже зібраного бінарника, ніж збирати з джерел.
        "llama_server_path": None,
    },
    "storage": {
        # Локальні файли -- єдиний бекенд. Зовнішнє сховище й БД -- поза
        # межами цієї частини роботи.
        "local_root": "data/output",
    },
    "review": {
        # 1 з N підтверджених документів -> на ручну перевірку (20 = 5%,
        # вимога architecture-proposal.md розд. 3).
        "sample_rate": 20,
    },
}

_PATH_KEYS = [
    ("paths", "input_dir"),
    ("paths", "output_dir"),
    ("paths", "schemas_dir"),
    ("paths", "dictionaries_dir"),
    ("paths", "llm_context"),
    ("intake", "processed_dir"),
    ("intake", "failed_dir"),
    ("storage", "local_root"),
]


def _merge(base: dict, override: dict) -> dict:
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path=None, project_root=None) -> dict:
    """Читає YAML поверх DEFAULTS. Відносні шляхи розгортаються від
    project_root (за замовчуванням -- каталог, що містить pipeline/), щоб
    запуск із будь-якої робочої директорії давав той самий результат."""
    cfg = deepcopy(DEFAULTS)
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            _merge(cfg, yaml.safe_load(f) or {})

    root = project_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg["project_root"] = root
    for section, key in _PATH_KEYS:
        value = cfg.get(section, {}).get(key)
        if value and not os.path.isabs(value):
            cfg[section][key] = os.path.normpath(os.path.join(root, value))
    model_path = cfg["llm"].get("model_path")
    if model_path and not os.path.isabs(model_path):
        cfg["llm"]["model_path"] = os.path.normpath(os.path.join(root, model_path))
    return cfg
