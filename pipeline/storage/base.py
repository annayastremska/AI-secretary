"""Контракт сховища документів. Локальна файлова система і MinIO
реалізують один і той самий інтерфейс з ОДНАКОВОЮ схемою ключів
(`documents/<domain>/<id>.md`), тому перехід "ноут -> MinIO клієнта" --
зміна одного рядка в конфізі, а не переписування пайплайна.
"""


class DocumentStore:
    def key_for(self, domain: str, document_id: str) -> str:
        return f"documents/{domain or 'unresolved'}/{document_id}.md"

    def find_by_hash(self, file_hash: str):
        """Ключ уже обробленого документа з таким самим вмістом, або None.
        Дедуплікація за хешем, а не за назвою файлу: те саме фото,
        завантажене двічі під різними іменами, не має дати два факти."""
        raise NotImplementedError

    def save(self, key: str, content: str, file_hash: str = None) -> str:
        raise NotImplementedError
