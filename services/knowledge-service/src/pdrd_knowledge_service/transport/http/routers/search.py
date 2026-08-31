# services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/search.py

"""Internal HTTP API Knowledge Service."""

from typing import Annotated

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
from pdrd_knowledge_service.transport.http.dependencies import (
    get_container,
)
from pdrd_knowledge_service.transport.http.schemas.search import (
    ExperienceSearchItemResponse,
    ExperienceSearchRequest,
    ExperienceSearchResponse,
    ExperienceSourceResponse,
    NormativeSearchRequest,
    NormativeSearchResponse,
    NormativeSourceResponse,
)

router = APIRouter(
    prefix="/internal/v1/search",
    tags=["knowledge-search"],
)


@router.post(
    "/normative",
    response_model=NormativeSearchResponse,
)
async def search_normative(
    request: NormativeSearchRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> NormativeSearchResponse:
    """Ищет нормативные требования для набора тем."""
    try:
        result = await container.search_normative.execute(
            request.queries,
        )
    except (
        EmbeddingProviderError,
        VectorStoreError,
    ) as error:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(error),
        ) from error

    return NormativeSearchResponse(
        queries=list(
            result.queries,
        ),
        sources=[
            NormativeSourceResponse(
                source_id=source.source_id,
                point_id=source.point_id,
                score=source.score,
                source_file=source.source_file,
                source_path=source.source_path,
                page=source.page,
                chunk_index=source.chunk_index,
                text=source.text,
            )
            for source in result.sources
        ],
        embedding_model=(result.embedding_model),
    )


@router.post(
    "/experience",
    response_model=ExperienceSearchResponse,
)
async def search_experience(
    request: ExperienceSearchRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> ExperienceSearchResponse:
    """Ищет похожие экспертные замечания."""
    try:
        results = await container.search_experience.execute(
            request.queries,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(error),
        ) from error
    except (
        EmbeddingProviderError,
        VectorStoreError,
    ) as error:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(error),
        ) from error

    return ExperienceSearchResponse(
        results=[
            ExperienceSearchItemResponse(
                query=result.query,
                sources=[
                    ExperienceSourceResponse(
                        source_id=source.source_id,
                        point_id=source.point_id,
                        score=source.score,
                        project_id=(source.project_id),
                        issue_id=source.issue_id,
                        issue_text=source.issue_text,
                        status=source.status,
                        verified_fixed=(source.verified_fixed),
                        before_page=(source.before_page),
                        after_page=source.after_page,
                        before_context=(source.before_context),
                        after_context=(source.after_context),
                    )
                    for source in result.sources
                ],
                embedding_model=(result.embedding_model),
            )
            for result in results
        ]
    )
