# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/normative.py

"""Поиск нормативных требований."""

import asyncio
from dataclasses import dataclass
from typing import Any

from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProvider,
)
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStore,
)
from pdrd_knowledge_service.domain.search import (
    NormativeSearchResult,
    NormativeSource,
    VectorPoint,
)

NORMATIVE_QUERY_INSTRUCTION = (
    "Given a description of a Russian engineering drawing or a technical "
    "check topic, retrieve the most directly applicable normative requirement "
    "for compliance verification."
)


@dataclass(frozen=True, slots=True)
class SearchNormative:
    """Ищет нормативы для набора тем проверки."""

    embedding_provider: EmbeddingProvider
    vector_store: VectorStore

    collection: str
    embedding_model: str

    top_k: int
    max_sources: int

    async def execute(
        self,
        queries: list[str],
    ) -> NormativeSearchResult:
        """Выполняет embedding и vector search нормативов."""
        normalized_queries = self._normalize_queries(
            queries,
        )

        if not normalized_queries:
            return NormativeSearchResult(
                queries=(),
                sources=(),
                embedding_model=self.embedding_model,
            )

        vectors = await self.embedding_provider.embed(
            normalized_queries,
            instruction=NORMATIVE_QUERY_INSTRUCTION,
        )

        groups = await asyncio.gather(
            *(
                self.vector_store.search(
                    collection=self.collection,
                    vector=vector,
                    limit=self.top_k,
                )
                for vector in vectors
            )
        )

        merged = self._merge_points(
            groups,
        )

        sources = tuple(
            self._build_source(
                point,
                index=index,
            )
            for index, point in enumerate(
                merged,
                start=1,
            )
        )

        return NormativeSearchResult(
            queries=normalized_queries,
            sources=sources,
            embedding_model=self.embedding_model,
        )

    @staticmethod
    def _normalize_queries(
        queries: list[str],
    ) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()

        for query in queries:
            normalized = query.strip()

            if not normalized or normalized in seen:
                continue

            seen.add(
                normalized,
            )

            result.append(
                normalized,
            )

        return tuple(
            result,
        )

    def _merge_points(
        self,
        groups: tuple[
            list[VectorPoint],
            ...,
        ],
    ) -> list[VectorPoint]:
        by_id: dict[str, VectorPoint] = {}

        for points in groups:
            for point in points:
                if not point.point_id:
                    continue

                previous = by_id.get(
                    point.point_id,
                )

                if previous is None or point.score > previous.score:
                    by_id[point.point_id] = point

        return sorted(
            by_id.values(),
            key=lambda point: point.score,
            reverse=True,
        )[: self.max_sources]

    @staticmethod
    def _build_source(
        point: VectorPoint,
        *,
        index: int,
    ) -> NormativeSource:
        payload = point.payload

        return NormativeSource(
            source_id=f"N{index}",
            point_id=point.point_id,
            score=round(
                point.score,
                4,
            ),
            source_file=SearchNormative._optional_string(
                payload.get(
                    "source_file",
                )
            ),
            source_path=SearchNormative._optional_string(
                payload.get(
                    "source_path",
                )
            ),
            page=SearchNormative._page_value(
                payload.get(
                    "page",
                )
            ),
            chunk_index=SearchNormative._page_value(
                payload.get(
                    "chunk_index",
                )
            ),
            text=str(
                payload.get(
                    "text",
                    "",
                )
                or ""
            ),
        )

    @staticmethod
    def _optional_string(
        value: Any,
    ) -> str | None:
        if value is None:
            return None

        return str(
            value,
        )

    @staticmethod
    def _page_value(
        value: Any,
    ) -> int | str | None:
        if isinstance(
            value,
            (int, str),
        ):
            return value

        return None
