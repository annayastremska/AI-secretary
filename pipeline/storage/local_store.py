"""Локальне файлове сховище -- робочий режим для ноутбука.

Схема ключів навмисно така сама, як планується в MinIO
(`documents/<domain>/<id>.md`), тому вміст каталогу data/output можна буде
перелити в бакет як є, без перейменувань.

Індекс хешів -- окремий append-only JSONL (`index/processed.jsonl`): не
база, але достатньо, щоб повторне завантаження того самого документа
розпізнавалось як дублікат, а не створювало другий факт.
"""
import json
import os

from pipeline.storage.base import DocumentStore

INDEX_REL_PATH = os.path.join("index", "processed.jsonl")


class LocalDocumentStore(DocumentStore):
    def __init__(self, root: str):
        self.root = root
        self.index_path = os.path.join(root, INDEX_REL_PATH)
        self._index = None

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
        return self._load_index().get(file_hash)

    def save(self, key: str, content: str, file_hash: str = None) -> str:
        path = os.path.join(self.root, key.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        if file_hash:
            os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
            with open(self.index_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"file_hash": file_hash, "key": key}, ensure_ascii=False) + "\n")
            self._load_index()[file_hash] = key
        return path
