# services/knowledge-service/tests/unit/test_experience_search.py

"""Unit-тесты retrieval Базы Опыта."""

import pytest
from pdrd_knowledge_service.application.use_cases.experience import (
    EXPERIENCE_QUERY_INSTRUCTION,
    SearchExperience,
)
from pdrd_knowledge_service.domain.search import VectorPoint


class FakeEmbeddingProvider:
    """Fake embedding provider для experience tests."""

    def __init__(self) -> None:
        """Инициализирует captured instruction."""
        self.instruction = ""

    async def embed(
        self,
        texts: tuple[str, ...],
        *,
        instruction: str,
    ) -> list[list[float]]:
        """Возвращает deterministic vectors."""
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
    """Fake vector storage для experience tests."""

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Возвращает legacy experience point."""
        assert collection == "experience"
        assert limit == 3
        assert vector

        return [
            VectorPoint(
                point_id="experience-1",
                score=0.81234,
                payload={
                    "project_id": "project-1",
                    "issue_id": "issue-1",
                    "issue_text": "Нет заземления",
                    "status": "fixed",
                    "verified_fixed": True,
                    "before_page": 4,
                    "after_page": 5,
                    "text": (
                        "Контекст листа до исправления:"
                        " металлический шкаф без PE."
                        "\n\nСтраница после исправления:"
                        " 5\n"
                        "Контекст исправленного листа:"
                        " добавлен PE-проводник."
                    ),
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


async def test_experience_search_preserves_legacy_context() -> None:
    """Проверяет совместимость со старым experience payload."""
    embedding = FakeEmbeddingProvider()

    use_case = SearchExperience(
        embedding_provider=embedding,
        vector_store=FakeVectorStore(),
        collection="experience",
        embedding_model="test-embedding",
        top_k=3,
    )

    results = await use_case.execute(
        [
            "Нет защитного заземления",
        ]
    )

    assert embedding.instruction == EXPERIENCE_QUERY_INSTRUCTION

    assert (
        len(
            results,
        )
        == 1
    )

    source = results[0].sources[0]

    assert source.source_id == "E1"
    assert source.score == 0.8123
    assert source.verified_fixed is True

    assert source.before_context == "металлический шкаф без PE."

    assert source.after_context == "добавлен PE-проводник."


async def test_experience_search_rejects_blank_query() -> None:
    """Проверяет отказ при пустом запросе среди нарушений."""
    use_case = SearchExperience(
        embedding_provider=FakeEmbeddingProvider(),
        vector_store=FakeVectorStore(),
        collection="experience",
        embedding_model="test-embedding",
        top_k=3,
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
