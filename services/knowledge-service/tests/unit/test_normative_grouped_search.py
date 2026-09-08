# services/knowledge-service/tests/unit/test_normative_grouped_search.py

"""Regression tests grouped normative HTTP retrieval TZ-5.2."""

from typing import Any

from pdrd_knowledge_service.domain.search import (
    NormativeSearchResult,
    NormativeSource,
)
from pdrd_knowledge_service.transport.http.routers.search import (
    search_normative_grouped,
)
from pdrd_knowledge_service.transport.http.schemas.search import (
    NormativeSearchRequest,
)


class FakeSearchNormative:
    """Fake use case grouped normative route."""

    def __init__(
        self,
    ) -> None:
        """Создаёт журнал вызовов."""
        self.calls: list[
            tuple[
                str,
                ...,
            ]
        ] = []

    async def execute(
        self,
        queries: list[str],
        *,
        section_id: Any = None,
        document_ids: Any = None,
    ) -> NormativeSearchResult:
        """Возвращает отдельный source для каждого вызова."""
        assert section_id is None
        assert document_ids is None
        assert (
            len(
                queries,
            )
            == 1
        )

        query = queries[0]

        self.calls.append(
            tuple(
                queries,
            )
        )

        index = len(
            self.calls,
        )

        source = NormativeSource(
            source_id="N1",
            point_id=f"point-{index}",
            score=0.9,
            document_id=f"document-{index}",
            section_id="section",
            category_id=None,
            source_sha256=str(
                index,
            )
            * 64,
            source_file=f"norm-{index}.pdf",
            source_path=None,
            page=index,
            chunk_index=index,
            text=f"Требование для {query}.",
        )

        return NormativeSearchResult(
            queries=(query,),
            sources=(source,),
            embedding_model="test-embedding",
        )


class FakeContainer:
    """Минимальный fake ApplicationContainer."""

    def __init__(
        self,
        search_normative: FakeSearchNormative,
    ) -> None:
        """Сохраняет fake use case."""
        self.search_normative = search_normative


async def test_grouped_normative_search_never_merges_queries() -> None:
    """Даже одинаковые queries получают независимые result groups."""
    search = FakeSearchNormative()

    response = await search_normative_grouped(
        NormativeSearchRequest(
            queries=[
                "маркировка",
                "маркировка",
            ]
        ),
        FakeContainer(
            search,
        ),  # type: ignore[arg-type]
    )

    assert search.calls == [
        ("маркировка",),
        ("маркировка",),
    ]

    assert (
        len(
            response.results,
        )
        == 2
    )

    assert response.results[0].sources[0].point_id == "point-1"

    assert response.results[1].sources[0].point_id == "point-2"

    assert response.results[0].sources[0].source_file == "norm-1.pdf"

    assert response.results[1].sources[0].source_file == "norm-2.pdf"
