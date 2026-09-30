# tests/experience_index_support.py

"""Тестовые порты E: версии источника, bounded embedding и содержимое векторного индекса.

HTTP-контракты и PostgreSQL проверяются отдельно; эти порты позволяют воспроизвести
изменение источника между embedding, записью и поиском без настоящей GPU.
"""

import hashlib
import json
from uuid import UUID

from pdrd_knowledge_service.application.ports.experience_feed import ExperienceFeedError
from pdrd_knowledge_service.application.ports.vector_store import StoredVectorPayload
from pdrd_knowledge_service.domain.search import VectorPoint
from pdrd_knowledge_service.infrastructure.experience_feed import parse_example

PNG = b"\x89PNG\r\n\x1a\nfixture"


def signed(data):
    """Пересчитывает именно отпечаток JSON, не имитируя служебную аутентификацию."""
    body = {key: value for key, value in data.items() if key != "fingerprint"}
    canonical = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return {**body, "fingerprint": hashlib.sha256(canonical.encode()).hexdigest()}


def trusted_example(*, number=1, tag="wise", target="", crops=1, revision=0):
    """Создаёт валидный контракт сервера с однозначным объектом отрицательной разметки."""
    negative = tag == "bad" or bool(target)
    original, current = "Первоначальный текст", "Текущий текст инженера"
    targets = ["original"] if negative else ["revised"]
    if target:
        targets = ["original", "revised"] if target == "both" else [target]
    return parse_example(
        signed(
            {
                "schema_version": 1,
                "example_id": str(UUID(int=number)),
                "example_revision": revision,
                "job_id": str(UUID(int=10)),
                "document_id": str(UUID(int=20)),
                "finding_id": f"vlm:{number}",
                "approved_revision": 3,
                "source_sha256": "a" * 64,
                "source_filename": "План.pdf",
                "page_number": 1,
                "tag": tag,
                "decision": "rejected" if negative else "accepted",
                "learning_use": "negative" if negative else "positive",
                "negative_target": target,
                "rejection_reason": "Ошибочное толкование" if target else "",
                "original_text": original,
                "text": current,
                "normative_basis": "СП 1 п. 2",
                "normative_reference": "",
                "texts": [
                    {
                        "target": item,
                        "text": original if item == "original" else current,
                    }
                    for item in targets
                ],
                "crops": [
                    {
                        "sha256": hashlib.sha256(PNG).hexdigest(),
                        "width": 120,
                        "height": 90,
                    }
                    for _ in range(crops)
                ],
            }
        )
    )


class MemoryFeed:
    """Версия текущего источника может меняться независимо от уже выданной страницы."""

    def __init__(self, examples=()):
        """Хранит страницы и текущие версии отдельно для проверки конкурентной правки."""
        self.examples = tuple(examples)
        self.current = {item.reference: item for item in examples}
        self.page_calls, self.verify_calls, self.crop_calls = [], [], []
        self.fail_page_after, self.fail_verify = None, False
        self.fail_crop = False

    async def page(self, *, after, limit):
        """Воспроизводит keyset-страницы и обрыв после уже обработанной страницы."""
        self.page_calls.append(after)
        if (
            self.fail_page_after is not None
            and len(self.page_calls) > self.fail_page_after
        ):
            raise ExperienceFeedError("Источник недоступен.")
        entries = sorted(
            (
                item
                for item in self.examples
                if after is None or item.reference.example_id > after
            ),
            key=lambda item: item.reference.example_id,
        )
        page = tuple(entries[:limit])
        return page, page[-1].reference.example_id if len(page) == limit else None

    async def verify(self, references):
        """Устаревшая ссылка исчезает до выдачи клиенту, а сбой не означает пустой успех."""
        self.verify_calls.append(references)
        if self.fail_verify:
            raise ExperienceFeedError("Проверка недоступна.")
        return tuple(self.current[item] for item in references if item in self.current)

    async def crop(self, *, example, index):
        """Возвращает отдельный crop, позволяя воспроизвести повреждение хранилища."""
        self.crop_calls.append((example.reference, index))
        if self.fail_crop:
            raise ExperienceFeedError("Повреждён crop.")
        return PNG


class RecordingEmbedding:
    """Записывает реальный контракт text/image и возвращает проверяемую размерность."""

    def __init__(self):
        """По умолчанию возвращает ненулевые двухмерные векторы."""
        self.calls, self.answer = [], None
        self.after_embed = None

    async def embed(self, inputs, *, instruction=None):
        """Можно изменить источник во время ожидания внешней embedding-модели."""
        self.calls.append((inputs, instruction))
        if self.after_embed:
            self.after_embed()
        return self.answer if self.answer is not None else [[1.0, 0.5] for _ in inputs]


class MemoryVectors:
    """Запоминает записи и удаления; deliberately возвращает payload без доверия к нему."""

    def __init__(self):
        """Не создаёт коллекцию автоматически, чтобы проверить первый запуск."""
        self.records, self.writes, self.deletes, self.searches = {}, [], [], []
        self.exists, self.points = False, None

    async def collection_exists(self, collection):
        """Проверяет наличие собственной коллекции."""
        return self.exists

    async def create_collection(self, *, collection, vector_size):
        """Фиксирует создание с нужной размерностью."""
        self.exists = True
        self.created = (collection, vector_size)

    async def scroll_payloads(self, *, collection):
        """Отдаёт метаданные без GPU и без vectors."""
        return tuple(
            StoredVectorPayload(item.point_id, item.payload)
            for item in self.records.values()
        )

    async def upsert(self, *, collection, records):
        """Повторная запись UUID заменяет точку, а не создаёт дубликат."""
        self.writes.append(records)
        self.records.update({item.point_id: item for item in records})

    async def delete_by_filter(self, *, collection, key, value):
        """Удаляет только явно выбранную точку."""
        self.deletes.append((key, value))
        self.records = {
            point: item
            for point, item in self.records.items()
            if item.payload.get(key) != value
        }

    async def search_filtered(self, **kwargs):
        """Записывает обязательный scope; тест также может подать нарушающие его точки."""
        self.searches.append(kwargs)
        return (
            self.points
            if self.points is not None
            else [
                VectorPoint(item.point_id, 0.9, item.payload)
                for item in self.records.values()
            ]
        )
