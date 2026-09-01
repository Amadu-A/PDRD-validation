# services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/project_context.py

"""Internal HTTP API temporary Project Context."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from pdrd_knowledge_service.application.ports.embedding import (
    EmbeddingProviderError,
)
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStoreError,
)
from pdrd_knowledge_service.core.container import (
    ApplicationContainer,
)
from pdrd_knowledge_service.domain.project_context import (
    ProjectContextError,
)
from pdrd_knowledge_service.transport.http.dependencies import (
    get_container,
)
from pdrd_knowledge_service.transport.http.schemas.project_context import (
    CreateProjectContextRequest,
    CreateProjectContextResponse,
    DeleteProjectContextResponse,
    ProjectContextSourcePayload,
    SearchProjectContextRequest,
    SearchProjectContextResponse,
)

router = APIRouter(
    prefix="/internal/v1/project-contexts",
    tags=["project-context"],
)


@router.post(
    "",
    response_model=(CreateProjectContextResponse),
)
async def create_project_context(
    request: CreateProjectContextRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> CreateProjectContextResponse:
    """Создаёт временный индекс ПЗ."""
    use_case = container.create_project_context

    if use_case is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Project Context indexing не настроен."),
        )

    try:
        result = await use_case.execute(
            context_id=(request.context_id),
            enabled=request.enabled,
            pages=tuple(page.to_domain() for page in request.pages),
        )

    except ProjectContextError as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(
                error,
            ),
        ) from error

    except (
        EmbeddingProviderError,
        VectorStoreError,
    ) as error:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(
                error,
            ),
        ) from error

    return CreateProjectContextResponse(
        context_id=result.context_id,
        enabled=result.enabled,
        collection_name=(result.collection_name),
        pages_count=(result.pages_count),
        chunks_count=(result.chunks_count),
        vector_size=(result.vector_size),
    )


@router.post(
    "/search",
    response_model=(SearchProjectContextResponse),
)
async def search_project_context(
    request: SearchProjectContextRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> SearchProjectContextResponse:
    """Ищет релевантный контекст ПЗ."""
    use_case = container.search_project_context

    if use_case is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Project Context search не настроен."),
        )

    try:
        result = await use_case.execute(
            context_id=(request.context_id),
            enabled=request.enabled,
            query=request.query,
        )

    except (
        ProjectContextError,
        EmbeddingProviderError,
        VectorStoreError,
    ) as error:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(
                error,
            ),
        ) from error

    return SearchProjectContextResponse(
        context_id=result.context_id,
        query=result.query,
        sources=[
            ProjectContextSourcePayload(
                source_id=(source.source_id),
                point_id=(source.point_id),
                score=source.score,
                page=source.page,
                chunk_index=(source.chunk_index),
                text=source.text,
            )
            for source in result.sources
        ],
        embedding_model=(result.embedding_model),
    )


@router.delete(
    "/{context_id}",
    response_model=(DeleteProjectContextResponse),
)
async def delete_project_context(
    context_id: UUID,
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> DeleteProjectContextResponse:
    """Идемпотентно удаляет временный индекс ПЗ."""
    use_case = container.delete_project_context

    if use_case is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Project Context cleanup не настроен."),
        )

    try:
        deleted = await use_case.execute(
            context_id=context_id,
        )

    except VectorStoreError as error:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(
                error,
            ),
        ) from error

    return DeleteProjectContextResponse(
        context_id=context_id,
        deleted=deleted,
    )
