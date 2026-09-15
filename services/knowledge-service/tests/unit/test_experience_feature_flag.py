# services/knowledge-service/tests/unit/test_experience_feature_flag.py

"""Тесты feature flag временно отключённой Базы опыта."""

from pdrd_knowledge_service.application.use_cases.experience import (
    SearchExperience,
)
from pdrd_knowledge_service.core.settings import (
    SearchSettings,
)
from pdrd_knowledge_service.domain.search import (
    VectorPoint,
)


class FailingEmbeddingProvider:
    """Embedding provider, который не должен вызываться при disabled E."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует счётчик вызовов."""
        self.calls = 0

    async def embed(
        self,
        texts: tuple[str, ...],
        *,
        instruction: str | None,
    ) -> list[list[float]]:
        """Падает, если disabled feature дошёл до embedding."""
        self.calls += 1

        raise AssertionError(
            ("Embedding provider не должен вызываться, когда База опыта выключена."),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness fake provider."""
        return True


class FailingVectorStore:
    """Vector store, который не должен искать E при disabled feature."""

    def __init__(
        self,
    ) -> None:
        """Инициализирует счётчик vector search."""
        self.search_calls = 0

    async def search(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
    ) -> list[VectorPoint]:
        """Падает, если disabled feature дошёл до Qdrant search."""
        self.search_calls += 1

        raise AssertionError(
            (
                "Vector search по Базе опыта не должен выполняться, "
                "когда feature flag выключен."
            ),
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness fake vector store."""
        return True

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Возвращает fake existence."""
        return bool(
            collection,
        )


def test_experience_retrieval_is_disabled_by_default() -> None:
    """До реализации feedback loop E retrieval выключен по умолчанию."""
    settings = SearchSettings()

    assert settings.experience_enabled is False


async def test_disabled_experience_preserves_contract_without_gpu_or_qdrant() -> None:
    """Disabled E возвращает пустые sources без embedding/vector retrieval."""
    embedding_provider = FailingEmbeddingProvider()

    vector_store = FailingVectorStore()

    use_case = SearchExperience(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        collection="dva_experience_active",
        embedding_model="test-embedding",
        top_k=3,
        enabled=False,
    )

    queries = [
        "Нет защитного заземления",
        "Нет защитного заземления",
        "Не указана маркировка",
    ]

    results = await use_case.execute(
        queries,
    )

    assert [result.query for result in results] == queries

    assert [result.sources for result in results] == [
        (),
        (),
        (),
    ]

    assert all(result.embedding_model == "test-embedding" for result in results)

    assert embedding_provider.calls == 0

    assert vector_store.search_calls == 0
