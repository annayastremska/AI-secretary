"""Локальне файлове сховище результатів.

Ключі мають вигляд `documents/<domain>/<id>.md` -- пласка, передбачувана
схема, яку легко перелити в будь-яке зовнішнє сховище пізніше (це вже поза
межами цієї частини роботи).

Індекс хешів -- окремий append-only JSONL (`index/processed.jsonl`): не
база, але достатньо, щоб повторне завантаження того самого документа
розпізнавалось як дублікат, а не створювало другий факт.
"""
import json
import os
import sys

INDEX_REL_PATH = os.path.join("index", "processed.jsonl")

# Блокування файлу індексу на час запису -- без нього два одночасні процеси
# (уже сьогодні можливо: `python run_pipeline.py` двічі паралельно на різні
# папки-приймачі, що пишуть у ТОЙ САМИЙ index/processed.jsonl; а `run.py`
# прямо каже, що виклик "у майбутньому з веб-бекенда" планується
# конкурентним) можуть переплести записи двох append() всередині одного
# рядка -- пошкоджений JSON-рядок, що `_load_index` мовчки пропускає
# (рядок 38), тобто той запис зникає з дедуплікації без жодного сигналу.
# НЕ закриває ширшу гонку "перевірка find_by_hash + пізніший save() не
# атомарні разом" -- два процеси все ще можуть одночасно вирішити, що той
# самий документ новий, і обидва його зберегти; це вимагало б блокування
# на весь process_file, не лише на сам запис, і лишається за межами цього
# фіксу, поки конкурентний виклик не став реальним сценарієм.
if sys.platform == "win32":
    import msvcrt

    def _lock_index_file(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_index_file(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_index_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock_index_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class LocalDocumentStore:
    def __init__(self, root: str):
        self.root = root
        self.index_path = os.path.join(root, INDEX_REL_PATH)
        self._index = None

    def key_for(self, domain: str, document_id: str) -> str:
        return f"documents/{domain or 'unresolved'}/{document_id}.md"

    def _load_index(self) -> dict:
        if self._index is None:
            self._index = {}
            if os.path.exists(self.index_path):
                with open(self.index_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue  # пошкоджений рядок не має валити весь прогін
                        if row.get("file_hash"):
                            self._index[row["file_hash"]] = row.get("key")
        return self._index

    def find_by_hash(self, file_hash: str):
        """Ключ уже обробленого документа з таким самим ВМІСТОМ, або None.
        Дедуплікація за хешем, а не за назвою файлу: те саме фото,
        завантажене двічі під різними іменами, не має дати два факти."""
        return self._load_index().get(file_hash)

    def retire(self, key: str):
        """Прибирає запис із «живих»: файл переїжджає з documents/ у
        superseded/ (той самий підшлях). Повертає новий шлях або None, якщо
        файла немає.

        Додано за R-A1-04: `--reprocess` лишав ДВА повні .md з одним
        file_hash у робочих теках -- споживач, що читає documents/**,
        порахував би той самий вміст двічі. Дані не губляться (файл лишається
        на диску), але живим лишається рівно один запис на вміст. Рядок
        індексу старого запису не чіпаємо: індекс append-only, при читанні
        перемагає останній запис (тобто новий ключ)."""
        old_path = os.path.join(self.root, key.replace("/", os.sep))
        if not os.path.exists(old_path):
            return None
        retired_key = key.replace("documents/", "superseded/", 1) \
            if key.startswith("documents/") else f"superseded/{key}"
        new_path = os.path.join(self.root, retired_key.replace("/", os.sep))
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        os.replace(old_path, new_path)
        return new_path

    def save(self, key: str, content: str, file_hash: str = None) -> str:
        path = os.path.join(self.root, key.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        if file_hash:
            os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
            with open(self.index_path, "a", encoding="utf-8") as f:
                _lock_index_file(f)
                try:
                    f.write(json.dumps({"file_hash": file_hash, "key": key}, ensure_ascii=False) + "\n")
                    f.flush()
                finally:
                    _unlock_index_file(f)
            self._load_index()[file_hash] = key
        return path
