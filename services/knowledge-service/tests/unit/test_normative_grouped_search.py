# services/knowledge-service/tests/unit/test_normative_grouped_search.py

"""Unit и synthetic-load tests grouped normative retrieval."""

from typing import Any

from pdrd_knowledge_service.application.use_cases.normative import (
    NORMATIVE_QUERY_INSTRUCTION,
    SearchNormative,
)
from pdrd_knowledge_service.domain.search import (
    NormativeSearchResult,
    NormativeSource,
    VectorPoint,
    VectorSearchFilter,
)
from pdrd_knowledge_service.transport.http.routers.search import (
    search_normative_grouped,
)
from pdrd_knowledge_service.transport.http.schemas.search import (
    NormativeSearchRequest,
)


class FakeSearchNormative:
    """Fake grouped normative use case HTTP route tests."""

    def __init__(
        self,
    ) -> None:
        """Создаёт журнал batch-вызовов."""
        self.calls: list[
            tuple[
                str,
                ...,
            ]
        ] = []

    async def execute_grouped(
        self,
        queries: list[str],
        *,
        section_id: Any = None,
        document_ids: Any = None,
    ) -> tuple[
        NormativeSearchResult,
        ...,
    ]:
        """Возвращает отдельную source group для каждой позиции."""
        assert section_id is None
        assert document_ids is None

        self.calls.append(
            tuple(
                queries,
            )
        )

        return tuple(
            NormativeSearchResult(
                queries=(query.strip(),),
                sources=(
                    NormativeSource(
                        source_id="N1",
                        point_id=f"point-{index}",
                        score=0.9,
                        document_id=f"document-{index}",
                        section_id="section",
                        category_id=None,
                        source_sha256=(
                            str(
                                index,
                            )
                            * 64
                        ),
                        source_file=f"norm-{index}.pdf",
                        source_path=None,
                        page=index,
                        chunk_index=index,
                        text=(f"Требование для {query.strip()}."),
                    ),
                ),
                embedding_model="test-embedding",
            )
            for index, query in enumerate(
                (query for query in queries if query.strip()),
                start=1,
            )
        )


class FakeContainer:
    """Минимальный fake ApplicationContainer."""

    def __init__(
        self,
        search_normative: FakeSearchNormative,
    ) -> None:
        """Сохраняет fake use case."""
        self.search_normative = search_normative


class RecordingEmbeddingProvider:
    """Fake provider с журналом embedding batch calls."""

    def __init__(
        self,
    ) -> None:
        """Создаёт журнал."""
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
        """Возвращает deterministic vector каждому уникальному query."""
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
        """Возвращает readiness."""
        return True


class RecordingVectorStore:
    """Fake Qdrant с отдельным point для каждого vector."""

    def __init__(
        self,
    ) -> None:
        """Создаёт журналы."""
        self.search_vectors: list[
            tuple[
                float,
                ...,
            ]
        ] = []

        self.filtered_vectors: list[
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
        """Возвращает source, соответствующий номеру vector."""
        assert collection == "normative-test"
        assert limit == 4

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
                point_id=f"point-{index}",
                score=0.9,
                payload={
                    "document_id": f"document-{index}",
                    "section_id": "section",
                    "source_file": f"norm-{index}.pdf",
                    "page": index,
                    "chunk_index": index,
                    "text": f"Требование {index}.",
                },
            )
        ]

    async def search_filtered(
        self,
        *,
        collection: str,
        vector: list[float],
        limit: int,
        search_filter: VectorSearchFilter,
    ) -> list[VectorPoint]:
        """Сохраняет filtered вызов и использует тот же fixture."""
        assert search_filter.must

        self.filtered_vectors.append(
            tuple(
                vector,
            )
        )

        return await self.search(
            collection=collection,
            vector=vector,
            limit=limit,
        )

    async def is_ready(
        self,
    ) -> bool:
        """Возвращает readiness."""
        return True

    async def collection_exists(
        self,
        collection: str,
    ) -> bool:
        """Возвращает существование collection."""
        return collection == "normative-test"


def _use_case(
    *,
    embedding: RecordingEmbeddingProvider,
    vector_store: RecordingVectorStore,
) -> SearchNormative:
    """Создаёт grouped SearchNormative fixture."""
    return SearchNormative(
        embedding_provider=embedding,
        vector_store=vector_store,
        collection="normative-test",
        embedding_model="test-embedding",
        top_k=4,
        max_sources=12,
    )


async def test_grouped_route_calls_application_batch_once() -> None:
    """HTTP route не запускает use case по одному finding."""
    search = FakeSearchNormative()

    response = await search_normative_grouped(
        NormativeSearchRequest(
            queries=[
                "маркировка",
                "заземление",
                "маркировка",
            ]
        ),
        FakeContainer(
            search,
        ),  # type: ignore[arg-type]
    )

    assert search.calls == [
        (
            "маркировка",
            "заземление",
            "маркировка",
        ),
    ]

    assert (
        len(
            response.results,
        )
        == 3
    )

    assert response.results[0].query == "маркировка"

    assert response.results[1].query == "заземление"

    assert response.results[2].query == "маркировка"

    assert response.results[0].sources[0].point_id == "point-1"

    assert response.results[1].sources[0].point_id == "point-2"

    assert response.results[2].sources[0].point_id == "point-3"


async def test_grouped_use_case_embeds_unique_queries_once() -> None:
    """Дубли не требуют повторного embedding, но позиции сохраняются."""
    embedding = RecordingEmbeddingProvider()

    vector_store = RecordingVectorStore()

    results = await _use_case(
        embedding=embedding,
        vector_store=vector_store,
    ).execute_grouped(
        [
            " маркировка ",
            "заземление",
            "маркировка",
            "",
        ]
    )

    assert embedding.calls == [
        (
            "маркировка",
            "заземление",
        ),
    ]

    assert embedding.instructions == [
        NORMATIVE_QUERY_INSTRUCTION,
    ]

    assert vector_store.search_vectors == [
        (1.0,),
        (2.0,),
    ]

    assert (
        len(
            results,
        )
        == 3
    )

    assert tuple(result.queries for result in results) == (
        ("маркировка",),
        ("заземление",),
        ("маркировка",),
    )

    first = results[0].sources

    second = results[1].sources

    third = results[2].sources

    assert first[0].point_id == "point-1"

    assert second[0].point_id == "point-2"

    assert third[0].point_id == "point-1"

    assert first == third

    assert first != second

    assert first[0].source_id == "N1"

    assert second[0].source_id == "N1"


async def test_grouped_use_case_never_merges_source_groups() -> None:
    """Sources одного query не протекают в другой finding."""
    embedding = RecordingEmbeddingProvider()

    vector_store = RecordingVectorStore()

    results = await _use_case(
        embedding=embedding,
        vector_store=vector_store,
    ).execute_grouped(
        [
            "маркировка кабелей",
            "защитное заземление",
        ]
    )

    assert (
        len(
            results,
        )
        == 2
    )

    assert {source.point_id for source in results[0].sources} == {
        "point-1",
    }

    assert {source.point_id for source in results[1].sources} == {
        "point-2",
    }


async def test_fifty_grouped_queries_use_one_embedding_batch() -> None:
    """Synthetic load: 50 findings не создают 50 model lifecycles."""
    embedding = RecordingEmbeddingProvider()

    vector_store = RecordingVectorStore()

    queries = [
        f"Проверка требования {index}"
        for index in range(
            1,
            51,
        )
    ]

    results = await _use_case(
        embedding=embedding,
        vector_store=vector_store,
    ).execute_grouped(
        queries,
    )

    assert (
        len(
            embedding.calls,
        )
        == 1
    )

    assert (
        len(
            embedding.calls[0],
        )
        == 50
    )

    assert embedding.calls[0] == tuple(
        queries,
    )

    assert (
        len(
            vector_store.search_vectors,
        )
        == 50
    )

    assert (
        len(
            results,
        )
        == 50
    )

    assert tuple(result.queries[0] for result in results) == tuple(
        queries,
    )

    assert tuple(result.sources[0].point_id for result in results) == tuple(
        f"point-{index}"
        for index in range(
            1,
            51,
        )
    )
