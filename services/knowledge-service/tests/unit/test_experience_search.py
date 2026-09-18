# services/knowledge-service/tests/unit/test_experience_search.py

"""Unit-тесты retrieval Базы Опыта."""

import pytest
from pdrd_knowledge_service.application.use_cases.experience import (
    EXPERIENCE_QUERY_INSTRUCTION,
    SearchExperience,
)
from pdrd_knowledge_service.domain.search import (
    VectorPoint,
)


class FakeEmbeddingProvider:
    """Fake embedding provider для experience tests."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует журнал embedding calls."""
        self.calls: list[
            tuple[
                str,
                ...,
            ]
        ] = []

        self.instructions: list[str | None] = []

    async def embed(
        self,
        texts: tuple[str, ...],
        *,
        instruction: str | None,
    ) -> list[list[float,]]:
        """Возвращает deterministic vector для каждого unique query."""
        self.calls.append(
            texts,
        )

        self.instructions.append(
            instruction,
        )

        return [
            [
                float(
                    index,
                )
            ]
            for index in range(
                1,
                len(
                    texts,
                )
                + 1,
            )
        ]

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает fake readiness."""
        return True


class FakeVectorStore:
    """Fake vector storage для experience tests."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует журнал Qdrant search calls."""
        self.search_vectors: list[
            tuple[
                float,
                ...,
            ]
        ] = []

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Возвращает deterministic legacy experience point."""
        assert collection == "experience"
        assert limit == 3
        assert vector

        self.search_vectors.append(
            tuple(
                vector,
            )
        )

        index = int(
            vector[0],
        )

        return [
            VectorPoint(
                point_id=f"experience-{index}",
                score=0.81234,
                payload={
                    "project_id": f"project-{index}",
                    "issue_id": f"issue-{index}",
                    "issue_text": (f"Экспертное замечание {index}"),
                    "status": "fixed",
                    "verified_fixed": True,
                    "before_page": 4,
                    "after_page": 5,
                    "text": (
                        "Контекст листа до исправления:"
                        f" нарушение {index}."
                        "\n\nСтраница после исправления:"
                        " 5\n"
                        "Контекст исправленного листа:"
                        f" исправление {index}."
                    ),
                },
            )
        ]

    async def is_ready(
        self,
    ) -> bool:
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


def _use_case(
    *,
    embedding: FakeEmbeddingProvider,
    vector_store: FakeVectorStore,
) -> SearchExperience:
    """Создаёт SearchExperience test fixture."""
    return SearchExperience(
        embedding_provider=embedding,
        vector_store=vector_store,
        collection="experience",
        embedding_model="test-embedding",
        top_k=3,
    )


async def test_experience_search_preserves_legacy_context() -> None:
    """Проверяет совместимость со старым experience payload."""
    embedding = FakeEmbeddingProvider()

    vector_store = FakeVectorStore()

    use_case = _use_case(
        embedding=embedding,
        vector_store=vector_store,
    )

    results = await use_case.execute(
        [
            "Нет защитного заземления",
        ]
    )

    assert embedding.calls == [
        ("Нет защитного заземления",),
    ]

    assert embedding.instructions == [
        EXPERIENCE_QUERY_INSTRUCTION,
    ]

    assert vector_store.search_vectors == [
        (1.0,),
    ]

    assert (
        len(
            results,
        )
        == 1
    )

    source = results[0].sources[0]

    assert source.source_id == "E1"

    assert source.point_id == "experience-1"

    assert source.score == 0.8123

    assert source.verified_fixed is True

    assert source.before_context == "нарушение 1."

    assert source.after_context == "исправление 1."


async def test_experience_search_rejects_blank_query() -> None:
    """Проверяет отказ при пустом запросе среди нарушений."""
    embedding = FakeEmbeddingProvider()

    vector_store = FakeVectorStore()

    use_case = _use_case(
        embedding=embedding,
        vector_store=vector_store,
    )

    with pytest.raises(
        ValueError,
    ):
        await use_case.execute(
            [
                "valid",
                " ",
            ]
        )

    assert embedding.calls == []

    assert vector_store.search_vectors == []


async def test_experience_search_deduplicates_exact_queries() -> None:
    """Одинаковый normalized query вычисляется только один раз."""
    embedding = FakeEmbeddingProvider()

    vector_store = FakeVectorStore()

    use_case = _use_case(
        embedding=embedding,
        vector_store=vector_store,
    )

    results = await use_case.execute(
        [
            " Нет защитного заземления ",
            "Отсутствует маркировка",
            "Нет защитного заземления",
        ]
    )

    assert embedding.calls == [
        (
            "Нет защитного заземления",
            "Отсутствует маркировка",
        ),
    ]

    assert embedding.instructions == [
        EXPERIENCE_QUERY_INSTRUCTION,
    ]

    assert vector_store.search_vectors == [
        (1.0,),
        (2.0,),
    ]

    assert [result.query for result in results] == [
        "Нет защитного заземления",
        "Отсутствует маркировка",
        "Нет защитного заземления",
    ]

    assert [result.sources[0].point_id for result in results] == [
        "experience-1",
        "experience-2",
        "experience-1",
    ]

    assert results[0].sources == results[2].sources

    assert results[0].sources != results[1].sources


async def test_fifty_experience_queries_embed_only_unique_values() -> None:
    """Synthetic load сохраняет 50 позиций при десяти unique queries."""
    embedding = FakeEmbeddingProvider()

    vector_store = FakeVectorStore()

    use_case = _use_case(
        embedding=embedding,
        vector_store=vector_store,
    )

    queries = [
        f"Замечание {index % 10}"
        for index in range(
            50,
        )
    ]

    results = await use_case.execute(
        queries,
    )

    assert (
        len(
            embedding.calls,
        )
        == 1
    )

    assert embedding.calls[0] == tuple(
        f"Замечание {index}"
        for index in range(
            10,
        )
    )

    assert (
        len(
            vector_store.search_vectors,
        )
        == 10
    )

    assert (
        len(
            results,
        )
        == 50
    )

    assert [result.query for result in results] == queries

    assert [result.sources[0].point_id for result in results[:10]] == [
        f"experience-{index}"
        for index in range(
            1,
            11,
        )
    ]

    assert (
        results[0].sources
        == results[10].sources
        == results[20].sources
        == results[30].sources
        == results[40].sources
    )


async def test_experience_search_preserves_order_after_deduplication() -> None:
    """Восстановление duplicate results не меняет исходный порядок."""
    embedding = FakeEmbeddingProvider()

    vector_store = FakeVectorStore()

    use_case = _use_case(
        embedding=embedding,
        vector_store=vector_store,
    )

    queries = [
        "B",
        "A",
        "B",
        "C",
        "A",
    ]

    results = await use_case.execute(
        queries,
    )

    assert embedding.calls == [
        (
            "B",
            "A",
            "C",
        ),
    ]

    assert [result.query for result in results] == queries

    assert [result.sources[0].point_id for result in results] == [
        "experience-1",
        "experience-2",
        "experience-1",
        "experience-3",
        "experience-2",
    ]
