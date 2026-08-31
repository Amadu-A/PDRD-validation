# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/experience.py

"""Поиск похожих экспертных замечаний."""

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
    ExperienceSearchResult,
    ExperienceSource,
    VectorPoint,
)

EXPERIENCE_QUERY_INSTRUCTION = (
    "Given an engineering design violation, retrieve similar expert review "
    "comments and correction examples."
)


@dataclass(frozen=True, slots=True)
class SearchExperience:
    """Ищет похожие примеры для найденных нарушений."""

    embedding_provider: EmbeddingProvider
    vector_store: VectorStore

    collection: str
    embedding_model: str

    top_k: int

    async def execute(
        self,
        queries: list[str],
    ) -> tuple[
        ExperienceSearchResult,
        ...,
    ]:
        """Выполняет независимый поиск для каждого нарушения."""
        normalized = tuple(query.strip() for query in queries)

        if not normalized:
            return ()

        if any(not query for query in normalized):
            raise ValueError(
                "Запрос к Базе Опыта не может быть пустым.",
            )

        vectors = await self.embedding_provider.embed(
            normalized,
            instruction=EXPERIENCE_QUERY_INSTRUCTION,
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

        return tuple(
            ExperienceSearchResult(
                query=query,
                sources=tuple(
                    self._build_source(
                        point,
                        index=index,
                    )
                    for index, point in enumerate(
                        points,
                        start=1,
                    )
                ),
                embedding_model=self.embedding_model,
            )
            for query, points in zip(
                normalized,
                groups,
                strict=True,
            )
        )

    @staticmethod
    def _build_source(
        point: VectorPoint,
        *,
        index: int,
    ) -> ExperienceSource:
        payload = point.payload

        raw_text = str(
            payload.get(
                "text",
                "",
            )
            or ""
        )

        (
            legacy_before,
            legacy_after,
        ) = SearchExperience._split_legacy_context(
            raw_text,
        )

        before_context = (
            SearchExperience._optional_string(
                payload.get(
                    "before_context",
                )
            )
            or legacy_before
        )

        after_context = (
            SearchExperience._optional_string(
                payload.get(
                    "after_context",
                )
            )
            or legacy_after
        )

        return ExperienceSource(
            source_id=f"E{index}",
            point_id=point.point_id,
            score=round(
                point.score,
                4,
            ),
            project_id=SearchExperience._optional_string(
                payload.get(
                    "project_id",
                )
            ),
            issue_id=SearchExperience._optional_string(
                payload.get(
                    "issue_id",
                )
            ),
            issue_text=SearchExperience._optional_string(
                payload.get(
                    "issue_text",
                )
            ),
            status=SearchExperience._optional_string(
                payload.get(
                    "status",
                )
            ),
            verified_fixed=bool(
                payload.get(
                    "verified_fixed",
                    False,
                )
            ),
            before_page=SearchExperience._page_value(
                payload.get(
                    "before_page",
                )
            ),
            after_page=SearchExperience._page_value(
                payload.get(
                    "after_page",
                )
            ),
            before_context=before_context,
            after_context=after_context,
        )

    @staticmethod
    def _split_legacy_context(
        text: str,
    ) -> tuple[str, str]:
        before_context = ""
        after_context = ""

        before_marker = "Контекст листа до исправления:"

        after_page_marker = "\n\nСтраница после исправления:"

        after_marker = "Контекст исправленного листа:"

        if before_marker not in text:
            return (
                before_context,
                after_context,
            )

        tail = text.split(
            before_marker,
            maxsplit=1,
        )[1]

        if after_page_marker not in tail:
            return (
                tail.strip(),
                after_context,
            )

        before_context, after_tail = tail.split(
            after_page_marker,
            maxsplit=1,
        )

        if after_marker in after_tail:
            after_context = after_tail.split(
                after_marker,
                maxsplit=1,
            )[1]

        return (
            before_context.strip(),
            after_context.strip(),
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
