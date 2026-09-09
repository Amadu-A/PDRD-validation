# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/user_packages.py

"""Semantic retrieval выбранных пользовательских документов."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_knowledge_service.application.use_cases.normative import (
    SearchNormative,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    CatalogArea,
)
from pdrd_knowledge_service.domain.search import (
    UserPackageSearchResult,
    UserPackageSource,
)

USER_PACKAGE_QUERY_INSTRUCTION = (
    "Given a description of an engineering drawing or a technical check topic, "
    "retrieve the most relevant fragment from the explicitly selected user "
    "documents. These documents are project or customer context and are not "
    "normative regulations."
)


@dataclass(frozen=True, slots=True)
class SearchUserPackages:
    """Ищет только в явно выбранных user-package документах."""

    managed_search: SearchNormative

    async def execute(
        self,
        queries: list[str],
        *,
        section_id: UUID | None,
        document_ids: list[UUID] | None,
    ) -> UserPackageSearchResult:
        """Возвращает U-sources либо пустой результат без package scope."""
        if section_id is None and document_ids is None:
            return UserPackageSearchResult(
                queries=tuple(query.strip() for query in queries if query.strip()),
                sources=(),
                embedding_model=(self.managed_search.embedding_model),
            )

        result = await self.managed_search.execute(
            queries,
            section_id=section_id,
            document_ids=document_ids,
            expected_area=CatalogArea.USER_PACKAGE,
            source_prefix="U",
            query_instruction=(USER_PACKAGE_QUERY_INSTRUCTION),
            allow_unscoped=False,
        )

        return UserPackageSearchResult(
            queries=result.queries,
            sources=tuple(
                UserPackageSource(
                    source_id=source.source_id,
                    point_id=source.point_id,
                    score=source.score,
                    document_id=source.document_id,
                    section_id=source.section_id,
                    category_id=source.category_id,
                    source_sha256=source.source_sha256,
                    source_file=source.source_file,
                    source_path=source.source_path,
                    page=source.page,
                    chunk_index=source.chunk_index,
                    text=source.text,
                )
                for source in result.sources
            ),
            embedding_model=result.embedding_model,
        )
