# services/knowledge-service/tests/unit/test_normative_search.py

"""Unit-тесты нормативного retrieval."""

from pdrd_knowledge_service.application.use_cases.normative import (
    NORMATIVE_QUERY_INSTRUCTION,
    SearchNormative,
)
from pdrd_knowledge_service.domain.search import VectorPoint


class FakeEmbeddingProvider:
    """Fake embedding provider для application tests."""

    def __init__(self) -> None:
        """Инициализирует captured calls."""
        self.texts: tuple[str, ...] = ()
        self.instruction = ""

    async def embed(
        self,
        texts: tuple[str, ...],
        *,
        instruction: str,
    ) -> list[list[float]]:
        """Возвращает deterministic vectors."""
        self.texts = texts
        self.instruction = instruction

        return [
            [
                float(index),
            ]
            for index in range(
                1,
                len(texts) + 1,
            )
        ]

    async def is_ready(self) -> bool:
        """Возвращает fake readiness."""
        return True


class FakeVectorStore:
    """Fake vector storage для normative tests."""

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Возвращает результаты по marker vector."""
        assert collection == "normative"
        assert limit == 4

        if vector == [1.0]:
            return [
                VectorPoint(
                    point_id="shared",
                    score=0.7,
                    payload={
                        "source_file": "old.pdf",
                        "page": 1,
                        "text": "Older duplicate",
                    },
                ),
                VectorPoint(
                    point_id="unique",
                    score=0.8,
                    payload={
                        "source_file": "unique.pdf",
                        "page": 3,
                        "chunk_index": 2,
                        "text": "Unique requirement",
                    },
                ),
            ]

        return [
            VectorPoint(
                point_id="shared",
                score=0.9,
                payload={
                    "source_file": "better.pdf",
                    "source_path": "/norms/better.pdf",
                    "page": 7,
                    "chunk_index": 4,
                    "text": "Better duplicate",
                },
            )
        ]

    async def is_ready(self) -> bool:
        """Возвращает fake readiness."""
        return True

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Возвращает fake collection existence."""
        return bool(
            collection,
        )


async def test_normative_search_deduplicates_queries_and_points() -> None:
    """Проверяет deduplication запросов и Qdrant point ids."""
    embedding = FakeEmbeddingProvider()

    use_case = SearchNormative(
        embedding_provider=embedding,
        vector_store=FakeVectorStore(),
        collection="normative",
        embedding_model="test-embedding",
        top_k=4,
        max_sources=12,
    )

    result = await use_case.execute(
        [
            " cable routing ",
            "cable routing",
            "",
            "grounding",
        ]
    )

    assert result.queries == (
        "cable routing",
        "grounding",
    )

    assert embedding.texts == result.queries
    assert embedding.instruction == NORMATIVE_QUERY_INSTRUCTION

    assert (
        len(
            result.sources,
        )
        == 2
    )

    first = result.sources[0]
    second = result.sources[1]

    assert first.source_id == "N1"
    assert first.point_id == "shared"
    assert first.score == 0.9
    assert first.source_file == "better.pdf"
    assert first.page == 7

    assert second.source_id == "N2"
    assert second.point_id == "unique"
    assert second.score == 0.8


async def test_empty_normative_search_does_not_call_embedding() -> None:
    """Проверяет быстрый ответ для пустого набора тем."""
    embedding = FakeEmbeddingProvider()

    use_case = SearchNormative(
        embedding_provider=embedding,
        vector_store=FakeVectorStore(),
        collection="normative",
        embedding_model="test-embedding",
        top_k=4,
        max_sources=12,
    )

    result = await use_case.execute(
        [
            "",
            "   ",
        ]
    )

    assert result.queries == ()
    assert result.sources == ()

    assert embedding.texts == ()
