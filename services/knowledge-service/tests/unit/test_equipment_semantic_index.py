# services/knowledge-service/tests/unit/test_equipment_semantic_index.py

"""Проверяет ограниченную локальную индексацию EQ snapshot и retrieval."""

import asyncio
from dataclasses import dataclass, field

import pytest
from pdrd_knowledge_service.application.equipment_fact_index import EquipmentFactIndex
from pdrd_knowledge_service.application.equipment_semantic_index import (
    EquipmentSemanticIndex,
)
from pdrd_knowledge_service.domain.search import VectorPoint
from pdrd_knowledge_service.infrastructure.database.equipment_facts import (
    PostgresEquipmentFactIndex,
)


@dataclass
class Embeddings:
    """Запоминает только число вызовов; не обращается к GPU."""

    batches: list[tuple[str, ...]] = field(default_factory=list)

    async def embed(self, texts: tuple[str, ...], *, instruction: str | None):
        """Возвращает предсказуемые фиктивные векторы."""
        self.batches.append(texts)
        return [[float(len(text)), 1.0] for text in texts]


@dataclass
class Vectors:
    """Имитирует Qdrant с изоляцией source ID и SHA."""

    collections: set[str] = field(default_factory=set)
    records: list = field(default_factory=list)
    searches: int = 0
    deleted: list[str] = field(default_factory=list)

    async def collection_exists(self, collection: str) -> bool:
        """Проверяет существование физической коллекции."""
        return collection in self.collections

    async def create_collection(self, *, collection: str, vector_size: int):
        """Сохраняет размер и имя коллекции."""
        assert vector_size == 2
        self.collections.add(collection)

    async def upsert(self, *, collection: str, records: tuple):
        """Сохраняет фрагменты с физической страницей."""
        assert collection in self.collections
        self.records.extend(records)

    async def search(self, *, collection: str, vector: list[float], limit: int):
        """Возвращает свою и чужую точку для проверки фильтра."""
        self.searches += 1
        assert limit <= 8
        own = self.records[-1]
        return [
            VectorPoint("other", 0.99, {**own.payload, "source_id": "EQ-other"}),
            VectorPoint(own.point_id, 0.9, own.payload),
        ]

    async def delete_collection(self, *, collection: str) -> bool:
        """Удаляет неудачно построенный индекс."""
        self.deleted.append(collection)
        self.collections.discard(collection)
        return True


@pytest.mark.asyncio
async def test_semantic_index_reuses_snapshot_collection_and_filters_scope() -> None:
    """Текст производителя индексируется один раз, query ограничен моделью."""
    embeddings = Embeddings()
    vectors = Vectors()
    index = EquipmentSemanticIndex(embeddings, vectors)
    args = {
        "source_id": "EQ-snapshot",
        "document_sha256": "a" * 64,
        "extractor_version": 5,
        "model": "DRC-100B",
        "properties": ("output_voltage",),
        "pages": [
            {"page": 1, "text": "DRC-100B output voltage 21...29 V DC"},
            {"page": 2, "text": "DRC-100B input voltage 180...264 V AC"},
        ],
    }

    assert await index.rank_pages(**args) == (2,)
    initial_records = len(vectors.records)
    assert await index.rank_pages(**args) == (2,)
    assert len(vectors.records) == initial_records
    assert len(embeddings.batches) == 3
    assert vectors.records[0].payload["source_kind"] == "EQ"
    assert vectors.records[0].payload["document_sha256"] == "a" * 64
    assert all("PS1" not in text for batch in embeddings.batches for text in batch)


@pytest.mark.asyncio
async def test_empty_scanned_text_does_not_call_embedding() -> None:
    """Для пустой страницы нет выдуманного вектора или факта."""
    embeddings = Embeddings()
    vectors = Vectors()
    index = EquipmentSemanticIndex(embeddings, vectors)
    pages = await index.rank_pages(
        source_id="EQ-scan",
        document_sha256="b" * 64,
        extractor_version=5,
        model="NXB-63",
        properties=(),
        pages=[{"page": 1, "text": ""}],
    )
    assert pages == ()
    assert embeddings.batches == []
    assert not vectors.collections


@pytest.mark.asyncio
async def test_cached_eq_facts_skip_embedding_and_retrieval(monkeypatch) -> None:
    """Повторный неизменённый snapshot не делает ни одного GPU-вызова."""
    embeddings = Embeddings()
    vectors = Vectors()
    semantic = EquipmentSemanticIndex(embeddings, vectors)
    index = EquipmentFactIndex(
        PostgresEquipmentFactIndex(None), semantic_index=semantic
    )

    async def cached_get(self, source_id: str):
        """Возвращает сохранённый набор текущей версии извлекателя."""
        return {
            "document_source_id": source_id,
            "document_sha256": "a" * 64,
            "manufacturer": "MEAN WELL",
            "model": "DRC-100B",
            "variant": "",
            "facts": [{"property_type": "output_voltage"}],
        }

    monkeypatch.setattr(PostgresEquipmentFactIndex, "get", cached_get)
    result = await index.extract_or_get(
        source_id="EQ-existing",
        sha256="a" * 64,
        manufacturer="MEAN WELL",
        model="DRC-100B",
        variant="",
        properties=("output_voltage",),
        pages=[{"page": 1, "text": "DRC-100B output voltage 24 V"}],
    )
    assert result["cache_hit"] is True
    assert embeddings.batches == []
    assert vectors.searches == 0


@pytest.mark.asyncio
async def test_cancellation_stops_next_embedding_batch_and_cleans_index() -> None:
    """После отмены новый GPU batch не запускается, частичный индекс удаляется."""
    embeddings = Embeddings()
    vectors = Vectors()
    index = EquipmentSemanticIndex(embeddings, vectors, embed_batch_size=1)
    checks = 0

    async def stopped() -> bool:
        """Разрешает первый batch и отменяет перед вторым."""
        nonlocal checks
        checks += 1
        return checks >= 3

    with pytest.raises(asyncio.CancelledError):
        await index.rank_pages(
            source_id="EQ-cancelled",
            document_sha256="c" * 64,
            extractor_version=5,
            model="DRC-100B",
            properties=("output_voltage",),
            pages=[
                {"page": 1, "text": "DRC-100B output voltage 21...29 V"},
                {"page": 2, "text": "DRC-100B input voltage 180...264 V"},
            ],
            should_stop=stopped,
        )

    assert len(embeddings.batches) == 1
    assert len(vectors.deleted) == 1
    assert not vectors.collections
