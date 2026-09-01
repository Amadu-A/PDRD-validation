# services/analysis-service/src/pdrd_analysis_service/transport/http/project_context_routes.py

"""Internal HTTP API Project Context Analysis Service."""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from pdrd_analysis_service.application.ports.vision_model import (
    VisionModelError,
)
from pdrd_analysis_service.core.container import (
    ApplicationContainer,
)
from pdrd_analysis_service.domain.project_context import (
    InvalidProjectContextError,
)
from pdrd_analysis_service.transport.http.dependencies import (
    get_container,
)
from pdrd_analysis_service.transport.http.project_context_schemas import (
    AugmentProjectContextRequest,
    AugmentProjectContextResponse,
    BuildProjectContextQueryRequest,
    BuildProjectContextQueryResponse,
    ProjectContextClassificationPayload,
    ProjectContextSourceExcerptPayload,
    ValidateProjectContextRequest,
    ValidateProjectContextResponse,
)

router = APIRouter(
    prefix="/internal/v1/project-context",
    tags=["project-context"],
)


@router.post(
    "/validate",
    response_model=(ValidateProjectContextResponse),
)
async def validate_project_context(
    request: ValidateProjectContextRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> ValidateProjectContextResponse:
    """Классифицирует выбранный диапазон ПЗ."""
    use_case = container.validate_project_context

    if use_case is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Project Context validation не настроен."),
        )

    try:
        (
            validation,
            metrics,
        ) = await use_case.execute(
            enabled=request.enabled,
            pages=tuple(page.to_domain() for page in request.pages),
        )

    except InvalidProjectContextError as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(
                error,
            ),
        ) from error

    except VisionModelError as error:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=str(
                error,
            ),
        ) from error

    classifications = [
        ProjectContextClassificationPayload(
            page_number=(item.page_number),
            kind=item.kind.value,
            confidence=(item.confidence),
            reason=item.reason,
        )
        for item in validation.classifications
    ]

    warnings = [
        ProjectContextClassificationPayload(
            page_number=(item.page_number),
            kind=item.kind.value,
            confidence=(item.confidence),
            reason=item.reason,
        )
        for item in validation.warnings
    ]

    return ValidateProjectContextResponse(
        enabled=validation.enabled,
        pages_count=(validation.pages_count),
        classifications=(classifications),
        warnings=warnings,
        metrics=[item.as_dict() for item in metrics],
    )


@router.post(
    "/query",
    response_model=(BuildProjectContextQueryResponse),
)
async def build_project_context_query(
    request: BuildProjectContextQueryRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> BuildProjectContextQueryResponse:
    """Строит semantic query для Knowledge Service."""
    use_case = container.build_project_context_query

    if use_case is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Project Context query builder не настроен."),
        )

    query = use_case.execute(
        page_facts=(request.page_facts.to_domain()),
        extracted_text=(request.extracted_text),
    )

    return BuildProjectContextQueryResponse(
        query=query,
    )


@router.post(
    "/augment",
    response_model=(AugmentProjectContextResponse),
)
async def augment_project_context(
    request: AugmentProjectContextRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> AugmentProjectContextResponse:
    """Добавляет PZ sources к нормативной проверке."""
    use_case = container.augment_project_context

    if use_case is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Project Context augmentation не настроен."),
        )

    result = use_case.execute(
        extracted_text=(request.extracted_text),
        sources=tuple(source.to_domain() for source in request.sources),
    )

    return AugmentProjectContextResponse(
        analysis_text=(result.analysis_text),
        project_context_texts=list(
            result.project_context_texts,
        ),
        sources=[
            ProjectContextSourceExcerptPayload(
                source_id=(source.source_id),
                score=source.score,
                page=source.page,
                chunk_index=(source.chunk_index),
                text_excerpt=(source.text[:500]),
            )
            for source in result.sources
        ],
    )
