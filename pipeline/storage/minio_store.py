"""MinIO-сховище. Той самий інтерфейс і та сама схема ключів, що в
LocalDocumentStore.

НЕ ПЕРЕВІРЕНО на живому MinIO -- у середовищі розробки немає ні сервера,
ні пакета `minio`. Код написаний за офіційним API пакета, але поводитись з
ним треба як з чернеткою до першого реального прогону.

Дедуплікація -- маркер-об'єкт `index/<file_hash>`, що містить ключ
документа: MinIO не має "запиту за вмістом", тож хеш стає частиною імені
об'єкта, і перевірка дубліката -- це один stat_object, без листингу бакета.
"""
import io

from pipeline.storage.base import DocumentStore


class MinioDocumentStore(DocumentStore):
    def __init__(self, endpoint, bucket, access_key, secret_key, secure=False):
        from minio import Minio  # лінивий імпорт: пакет потрібен лише для цього бекенда

        self.bucket = bucket
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)

    def find_by_hash(self, file_hash: str):
        from minio.error import S3Error

        try:
            resp = self.client.get_object(self.bucket, f"index/{file_hash}")
            try:
                return resp.read().decode("utf-8").strip() or None
            finally:
                resp.close()
                resp.release_conn()
        except S3Error:
            return None

    def _put(self, key: str, payload: bytes, content_type: str):
        self.client.put_object(self.bucket, key, io.BytesIO(payload), length=len(payload),
                               content_type=content_type)

    def save(self, key: str, content: str, file_hash: str = None) -> str:
        self._put(key, content.encode("utf-8"), "text/markdown; charset=utf-8")
        if file_hash:
            self._put(f"index/{file_hash}", key.encode("utf-8"), "text/plain; charset=utf-8")
        return f"s3://{self.bucket}/{key}"
