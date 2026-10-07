# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/document_context.py

"""Построение векторного D-контекста проверяемого PDF."""

from dataclasses import dataclass
from uuid import UUID

from pdrd_knowledge_service.application.use_cases.project_context import (
    CreateProjectContext,
)
from pdrd_knowledge_service.domain.project_context import (
    ProjectContextInfo,
    ProjectContextTextPage,
)


@dataclass(frozen=True, slots=True)
class DocumentContextPage:
    """Физическая страница и извлечённые атомарные факты."""

    page_number: int
    text: str
    facts: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class BuildDocumentContext:
    """Индексирует текст и факты один раз для всего PDF."""

    create_context: CreateProjectContext
    cache_enabled: bool

    async def execute(
        self,
        *,
        document_id: UUID,
        source_sha256: str,
        pages: tuple[DocumentContextPage, ...],
    ) -> ProjectContextInfo:
        """Возвращает reusable Qdrant collection с D-chunks."""
        if len(source_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in source_sha256.lower()
        ):
            raise ValueError("Неверный SHA256 исходного PDF.")
        if not pages or len({page.page_number for page in pages}) != len(pages):
            raise ValueError("Страницы D-контекста пусты либо повторяются.")

        index_pages = tuple(
            ProjectContextTextPage(
                page_number=page.page_number,
                text=(
                    (f"SHA256 PDF: {source_sha256}\n" if index == 0 else "")
                    + page.text
                    + "\n\nФАКТЫ СТРАНИЦЫ:\n"
                    + "\n".join(
                        "; ".join(
                            str(fact.get(field, "") or "").strip()
                            for field in (
                                "kind",
                                "subject_type",
                                "subject_name",
                                "identifier",
                                "property_type",
                                "property_name",
                                "value_raw",
                                "unit_raw",
                                "scope_system",
                                "scope_location",
                                "scope_segment",
                                "scope_operating_mode",
                                "scope_condition",
                                "table_title",
                                "table_id",
                                "row_label",
                                "column_label",
                                "evidence_text",
                            )
                        )
                        for fact in page.facts
                    )
                ),
            )
            for index, page in enumerate(
                sorted(pages, key=lambda item: item.page_number)
            )
        )
        if not self.cache_enabled:
            index_pages = (
                ProjectContextTextPage(
                    page_number=index_pages[0].page_number,
                    text=f"Задание {document_id}\n" + index_pages[0].text,
                ),
                *index_pages[1:],
            )
        return await self.create_context.execute(
            context_id=document_id,
            enabled=True,
            pages=index_pages,
        )
