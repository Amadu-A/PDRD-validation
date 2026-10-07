# services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/document_context.py

"""Закрытый HTTP-контракт D-контекста проверяемого PDF."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from pdrd_knowledge_service.application.ports.embedding import EmbeddingProviderError
from pdrd_knowledge_service.application.ports.vector_store import VectorStoreError
from pdrd_knowledge_service.application.use_cases.document_context import (
    DocumentContextPage,
)
from pdrd_knowledge_service.core.container import ApplicationContainer
from pdrd_knowledge_service.domain.project_context import ProjectContextError
from pdrd_knowledge_service.transport.http.dependencies import get_container
from pdrd_knowledge_service.transport.http.schemas.project_context import (
    ProjectContextSourcePayload,
)

router = APIRouter(prefix="/internal/v1/document-contexts", tags=["document-context"])


class DocumentContextPagePayload(BaseModel):
    """Страница PDF и её структурированные факты."""

    model_config = ConfigDict(extra="forbid")
    page_number: int = Field(ge=1)
    text: str
    document_facts: list[dict[str, object]] = Field(default_factory=list)

    def to_domain(self) -> DocumentContextPage:
        """Преобразует страницу в application DTO."""
        return DocumentContextPage(
            page_number=self.page_number,
            text=self.text,
            facts=tuple(self.document_facts),
        )


class BuildDocumentContextRequest(BaseModel):
    """Запрос на однократную индексацию выбранных страниц PDF."""

    model_config = ConfigDict(extra="forbid")
    document_id: UUID
    source_sha256: str = Field(min_length=64, max_length=64)
    pages: list[DocumentContextPagePayload] = Field(min_length=1, max_length=200)


class BuildDocumentContextResponse(BaseModel):
    """Идентификатор и размер reusable D-индекса."""

    context_id: UUID
    cache_hit: bool
    pages_count: int
    chunks_count: int


class SearchDocumentContextRequest(BaseModel):
    """Запрос семантического поиска в одном PDF."""

    model_config = ConfigDict(extra="forbid")
    context_id: UUID
    query: str


class SearchDocumentContextResponse(BaseModel):
    """Ограниченные D-sources с указанием физических страниц."""

    sources: list[ProjectContextSourcePayload]


@router.post("", response_model=BuildDocumentContextResponse)
async def build_document_context(
    request: BuildDocumentContextRequest,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> BuildDocumentContextResponse:
    """Индексирует текст и факты без изменения каталога N/T/U/E."""
    use_case = container.build_document_context
    if use_case is None:
        return BuildDocumentContextResponse(
            context_id=request.document_id,
            cache_hit=False,
            pages_count=len(request.pages),
            chunks_count=0,
        )
    try:
        result = await use_case.execute(
            document_id=request.document_id,
            source_sha256=request.source_sha256,
            pages=tuple(page.to_domain() for page in request.pages),
        )
    except (ValueError, ProjectContextError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except (EmbeddingProviderError, VectorStoreError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return BuildDocumentContextResponse(
        context_id=result.context_id,
        cache_hit=result.cache_hit,
        pages_count=result.pages_count,
        chunks_count=result.chunks_count,
    )


@router.post("/search", response_model=SearchDocumentContextResponse)
async def search_document_context(
    request: SearchDocumentContextRequest,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> SearchDocumentContextResponse:
    """Ищет релевантные D-chunks только в указанном PDF."""
    use_case = container.search_document_context
    if use_case is None:
        return SearchDocumentContextResponse(sources=[])
    try:
        result = await use_case.execute(
            context_id=request.context_id,
            enabled=True,
            query=request.query,
        )
    except (ProjectContextError, EmbeddingProviderError, VectorStoreError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    return SearchDocumentContextResponse(
        sources=[
            ProjectContextSourcePayload(
                source_id=source.source_id,
                point_id=source.point_id,
                score=source.score,
                page=source.page,
                chunk_index=source.chunk_index,
                text=source.text,
            )
            for source in result.sources
        ]
    )
