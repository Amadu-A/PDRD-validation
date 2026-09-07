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
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingProviderError,
)
from pdrd_knowledge_service.application.ports.vector_store import (
    VectorStoreError,
)
from pdrd_knowledge_service.application.use_cases.normative import (
    NormativeSearchScopeConflictError,
    NormativeSearchScopeError,
)
from pdrd_knowledge_service.application.use_cases.normative_sections import (
    NormativeSectionNotFoundError,
)
from pdrd_knowledge_service.application.use_cases.technical_assignment_retrieval import (
    TechnicalAssignmentSearchConflictError,
    TechnicalAssignmentSearchScopeError,
)
from pdrd_knowledge_service.application.use_cases.technical_assignments import (
    TechnicalAssignmentNotFoundError,
)
from pdrd_knowledge_service.core.container import (
    ApplicationContainer,
)
from pdrd_knowledge_service.domain.search import (
    NormativeSource,
)
from pdrd_knowledge_service.transport.http.dependencies import (
    get_container,
)
from pdrd_knowledge_service.transport.http.schemas.search import (
    ExperienceSearchItemResponse,
    ExperienceSearchRequest,
    ExperienceSearchResponse,
    ExperienceSourceResponse,
    NormativeReferenceResolutionResponse,
    NormativeSearchRequest,
    NormativeSearchResponse,
    NormativeSourceResponse,
    RetrievalDiagnosticResponse,
    TechnicalAssignmentConflictCandidateResponse,
    TechnicalAssignmentGuidedSearchRequest,
    TechnicalAssignmentGuidedSearchResponse,
    TechnicalAssignmentSourceResponse,
    UserPackageSearchRequest,
    UserPackageSearchResponse,
    UserPackageSourceResponse,
)

router = APIRouter(
    prefix="/internal/v1/search",
    tags=[
        "knowledge-search",
    ],
)


def _translate_managed_search_error(
    error: Exception,
) -> HTTPException:
    """Преобразует managed retrieval error в HTTP contract."""
    if isinstance(
        error,
        (
            NormativeSectionNotFoundError,
            TechnicalAssignmentNotFoundError,
        ),
    ):
        return HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND),
            detail=str(
                error,
            ),
        )

    if isinstance(
        error,
        (
            NormativeSearchScopeError,
            TechnicalAssignmentSearchScopeError,
        ),
    ):
        return HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(
                error,
            ),
        )

    if isinstance(
        error,
        (
            NormativeSearchScopeConflictError,
            TechnicalAssignmentSearchConflictError,
        ),
    ):
        return HTTPException(
            status_code=(status.HTTP_409_CONFLICT),
            detail=str(
                error,
            ),
        )

    return HTTPException(
        status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
        detail=str(
            error,
        ),
    )


def _normative_source_response(
    source: NormativeSource,
) -> NormativeSourceResponse:
    """Преобразует domain N source в HTTP schema."""
    return NormativeSourceResponse(
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


@router.post(
    "/normative",
    response_model=NormativeSearchResponse,
)
async def search_normative(
    request: NormativeSearchRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> NormativeSearchResponse:
    """Ищет только нормативные managed документы."""
    try:
        result = await container.search_normative.execute(
            request.queries,
            section_id=request.section_id,
            document_ids=request.document_ids,
        )

    except (
        NormativeSectionNotFoundError,
        NormativeSearchScopeError,
        NormativeSearchScopeConflictError,
        EmbeddingProviderError,
        VectorStoreError,
    ) as error:
        raise _translate_managed_search_error(
            error,
        ) from error

    return NormativeSearchResponse(
        queries=list(
            result.queries,
        ),
        sources=[
            _normative_source_response(
                source,
            )
            for source in result.sources
        ],
        embedding_model=result.embedding_model,
    )


@router.post(
    "/user-packages",
    response_model=UserPackageSearchResponse,
)
async def search_user_packages(
    request: UserPackageSearchRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> UserPackageSearchResponse:
    """Ищет только в явно выбранных user-package документах."""
    try:
        result = await container.search_user_packages.execute(
            request.queries,
            section_id=request.section_id,
            document_ids=request.document_ids,
        )

    except (
        NormativeSectionNotFoundError,
        NormativeSearchScopeError,
        NormativeSearchScopeConflictError,
        EmbeddingProviderError,
        VectorStoreError,
    ) as error:
        raise _translate_managed_search_error(
            error,
        ) from error

    return UserPackageSearchResponse(
        queries=list(
            result.queries,
        ),
        sources=[
            UserPackageSourceResponse(
                source_id=source.source_id,
                point_id=source.point_id,
                score=source.score,
                document_id=source.document_id,
                section_id=source.section_id,
                category_id=source.category_id,
                source_sha256=(source.source_sha256),
                source_file=source.source_file,
                source_path=source.source_path,
                page=source.page,
                chunk_index=source.chunk_index,
                text=source.text,
            )
            for source in result.sources
        ],
        embedding_model=result.embedding_model,
    )


@router.post(
    "/technical-assignment-guided",
    response_model=(TechnicalAssignmentGuidedSearchResponse),
)
async def search_technical_assignment_guided(
    request: TechnicalAssignmentGuidedSearchRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> TechnicalAssignmentGuidedSearchResponse:
    """Выполняет T-guided targeted + general N retrieval."""
    use_case = container.search_technical_assignment_guided

    if use_case is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("T-guided retrieval не настроен."),
        )

    try:
        result = await use_case.execute(
            request.queries,
            technical_assignment_id=(request.technical_assignment_id),
            section_id=request.section_id,
            normative_document_ids=(request.normative_document_ids),
        )

    except (
        TechnicalAssignmentNotFoundError,
        TechnicalAssignmentSearchScopeError,
        TechnicalAssignmentSearchConflictError,
        NormativeSectionNotFoundError,
        NormativeSearchScopeError,
        NormativeSearchScopeConflictError,
        EmbeddingProviderError,
        MultimodalEmbeddingProviderError,
        VectorStoreError,
    ) as error:
        raise _translate_managed_search_error(
            error,
        ) from error

    return TechnicalAssignmentGuidedSearchResponse(
        queries=list(
            result.queries,
        ),
        technical_assignment_sources=[
            TechnicalAssignmentSourceResponse(
                source_id=source.source_id,
                point_id=source.point_id,
                score=source.score,
                technical_assignment_id=(source.technical_assignment_id),
                analysis_document_id=(source.analysis_document_id),
                section_id=source.section_id,
                source_sha256=(source.source_sha256),
                source_file=source.source_file,
                page=source.page,
                text=source.text,
                normative_refs=list(
                    source.normative_refs,
                ),
            )
            for source in (result.technical_assignment_sources)
        ],
        reference_resolutions=[
            NormativeReferenceResolutionResponse(
                reference=resolution.reference,
                normalized_reference=(resolution.normalized_reference),
                status=resolution.status.value,
                document_ids=list(
                    resolution.document_ids,
                ),
                source_files=list(
                    resolution.source_files,
                ),
            )
            for resolution in (result.reference_resolutions)
        ],
        targeted_normative_sources=[
            _normative_source_response(
                source,
            )
            for source in (result.targeted_normative_sources)
        ],
        general_normative_sources=[
            _normative_source_response(
                source,
            )
            for source in (result.general_normative_sources)
        ],
        normative_sources=[
            _normative_source_response(
                source,
            )
            for source in (result.normative_sources)
        ],
        diagnostics=[
            RetrievalDiagnosticResponse(
                code=diagnostic.code,
                message=diagnostic.message,
                source_id=diagnostic.source_id,
                reference=diagnostic.reference,
            )
            for diagnostic in result.diagnostics
        ],
        conflict_candidates=[
            TechnicalAssignmentConflictCandidateResponse(
                technical_assignment_source_id=(
                    candidate.technical_assignment_source_id
                ),
                normative_source_ids=list(
                    candidate.normative_source_ids,
                ),
                reason=candidate.reason,
            )
            for candidate in (result.conflict_candidates)
        ],
        technical_assignment_embedding_model=(
            result.technical_assignment_embedding_model
        ),
        normative_embedding_model=(result.normative_embedding_model),
    )


@router.post(
    "/experience",
    response_model=ExperienceSearchResponse,
)
async def search_experience(
    request: ExperienceSearchRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
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

    return ExperienceSearchResponse(
        results=[
            ExperienceSearchItemResponse(
                query=result.query,
                sources=[
                    ExperienceSourceResponse(
                        source_id=source.source_id,
                        point_id=source.point_id,
                        score=source.score,
                        project_id=source.project_id,
                        issue_id=source.issue_id,
                        issue_text=source.issue_text,
                        status=source.status,
                        verified_fixed=(source.verified_fixed),
                        before_page=(source.before_page),
                        after_page=(source.after_page),
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
