# services/analysis-service/src/pdrd_analysis_service/transport/http/stage_batch_routes.py

"""Stage-scoped GPU HTTP API для больших PDF-документов."""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from pdrd_analysis_service.core.container import (
    ApplicationContainer,
)
from pdrd_analysis_service.transport.http.dependencies import (
    get_container,
)
from pdrd_analysis_service.transport.http.routes import (
    check_norms,
    finalize_findings,
    understand_page,
)
from pdrd_analysis_service.transport.http.stage_batch_schemas import (
    CheckNormsStageItemResponse,
    CheckNormsStageRequest,
    CheckNormsStageResponse,
    CheckTechnicalAssignmentStageItemResponse,
    CheckTechnicalAssignmentStageRequest,
    CheckTechnicalAssignmentStageResponse,
    FinalizeStageItemResponse,
    FinalizeStageRequest,
    FinalizeStageResponse,
    UnderstandPagesStageItemResponse,
    UnderstandPagesStageRequest,
    UnderstandPagesStageResponse,
)
from pdrd_analysis_service.transport.http.technical_assignment_routes import (
    check_technical_assignment,
)
from pdrd_analysis_service.transport.http.technical_assignment_schemas import (
    CheckTechnicalAssignmentRequest,
)

router = APIRouter()

logger = logging.getLogger(
    "uvicorn.error",
)

_UNDERSTANDING_STAGE = "understanding_sheet"
_CHECKING_STAGE = "checking_requirements"
_FINALIZATION_STAGE = "finalizing_findings"


def _validate_stage_size(
    *,
    actual: int,
    maximum: int,
) -> None:
    """Не позволяет одному stage превысить configured page limit."""
    if actual <= maximum:
        return

    raise HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        detail=(
            "Количество страниц GPU stage "
            f"({actual}) превышает configured limit "
            f"({maximum})."
        ),
    )


async def _ensure_stage_active(
    *,
    container: ApplicationContainer,
    document_id: UUID,
    stage: str,
    current: int,
    total: int,
) -> None:
    """Проверяет cancellation перед запуском следующей страницы."""
    logger.info(
        ("gpu_stage_page_start document_id=%s stage=%s current=%s total=%s"),
        document_id,
        stage,
        current,
        total,
    )

    probe = container.analysis_progress_probe

    if probe is None:
        return

    cancelled = await probe.is_cancelled(
        document_id=document_id,
        stage=stage,
        current=current,
        total=total,
    )

    if not cancelled:
        return

    logger.info(
        ("gpu_stage_cancelled document_id=%s stage=%s before_page=%s total=%s"),
        document_id,
        stage,
        current,
        total,
    )

    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "analysis_cancelled",
            "message": (
                f"Analysis job отменён до запуска страницы {current} из {total}."
            ),
        },
    )


@router.post(
    "/internal/v1/stages/understand-pages",
    response_model=UnderstandPagesStageResponse,
)
async def understand_pages_stage(
    request: UnderstandPagesStageRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> UnderstandPagesStageResponse:
    """Последовательно понимает все страницы под одним GPU residency scope."""
    _validate_stage_size(
        actual=len(
            request.items,
        ),
        maximum=container.settings.pipeline.max_stage_pages,
    )

    total = len(
        request.items,
    )

    result: list[UnderstandPagesStageItemResponse] = []

    for index, item in enumerate(
        request.items,
        start=1,
    ):
        await _ensure_stage_active(
            container=container,
            document_id=request.document_id,
            stage=_UNDERSTANDING_STAGE,
            current=index,
            total=total,
        )

        response = await understand_page(
            request=item,
            container=container,
        )

        result.append(
            UnderstandPagesStageItemResponse(
                page_number=item.page_number,
                result=response,
            )
        )

    logger.info(
        ("gpu_stage_complete document_id=%s stage=%s pages=%s"),
        request.document_id,
        _UNDERSTANDING_STAGE,
        total,
    )

    return UnderstandPagesStageResponse(
        items=result,
    )


@router.post(
    "/internal/v1/stages/check-technical-assignment",
    response_model=CheckTechnicalAssignmentStageResponse,
)
async def check_technical_assignment_stage(
    request: CheckTechnicalAssignmentStageRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> CheckTechnicalAssignmentStageResponse:
    """Выполняет T-first для всех страниц под одним GPU residency scope."""
    _validate_stage_size(
        actual=len(
            request.items,
        ),
        maximum=container.settings.pipeline.max_stage_pages,
    )

    total = len(
        request.items,
    )

    result: list[CheckTechnicalAssignmentStageItemResponse] = []

    for index, item in enumerate(
        request.items,
        start=1,
    ):
        await _ensure_stage_active(
            container=container,
            document_id=request.document_id,
            stage=_UNDERSTANDING_STAGE,
            current=index,
            total=total,
        )

        response = await check_technical_assignment(
            request=CheckTechnicalAssignmentRequest(
                page_number=item.page_number,
                extracted_text=item.extracted_text,
                page_facts=item.page_facts,
                image_base64=item.image_base64,
                technical_assignment_id=(request.technical_assignment_id),
                analysis_document_id=(request.analysis_document_id),
                section_id=request.section_id,
                source_file=request.source_file,
                source_sha256=request.source_sha256,
                requirements=request.requirements,
            ),
            container=container,
        )

        result.append(
            CheckTechnicalAssignmentStageItemResponse(
                page_number=item.page_number,
                result=response,
            )
        )

    logger.info(
        ("gpu_stage_complete document_id=%s stage=technical_assignment pages=%s"),
        request.document_id,
        total,
    )

    return CheckTechnicalAssignmentStageResponse(
        items=result,
    )


@router.post(
    "/internal/v1/stages/check-norms",
    response_model=CheckNormsStageResponse,
)
async def check_norms_stage(
    request: CheckNormsStageRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> CheckNormsStageResponse:
    """Проверяет N/T/U всех страниц под одним GPU residency scope."""
    _validate_stage_size(
        actual=len(
            request.items,
        ),
        maximum=container.settings.pipeline.max_stage_pages,
    )

    total = len(
        request.items,
    )

    result: list[CheckNormsStageItemResponse] = []

    for index, item in enumerate(
        request.items,
        start=1,
    ):
        await _ensure_stage_active(
            container=container,
            document_id=request.document_id,
            stage=_CHECKING_STAGE,
            current=index,
            total=total,
        )

        response = await check_norms(
            request=item,
            container=container,
        )

        result.append(
            CheckNormsStageItemResponse(
                page_number=item.page_number,
                result=response,
            )
        )

    logger.info(
        ("gpu_stage_complete document_id=%s stage=%s pages=%s"),
        request.document_id,
        _CHECKING_STAGE,
        total,
    )

    return CheckNormsStageResponse(
        items=result,
    )


@router.post(
    "/internal/v1/stages/finalize",
    response_model=FinalizeStageResponse,
)
async def finalize_stage(
    request: FinalizeStageRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> FinalizeStageResponse:
    """Финализирует все страницы под одним GPU residency scope."""
    _validate_stage_size(
        actual=len(
            request.items,
        ),
        maximum=container.settings.pipeline.max_stage_pages,
    )

    total = len(
        request.items,
    )

    result: list[FinalizeStageItemResponse] = []

    for index, item in enumerate(
        request.items,
        start=1,
    ):
        await _ensure_stage_active(
            container=container,
            document_id=request.document_id,
            stage=_FINALIZATION_STAGE,
            current=index,
            total=total,
        )

        response = await finalize_findings(
            request=item.request,
            container=container,
        )

        result.append(
            FinalizeStageItemResponse(
                page_number=item.page_number,
                result=response,
            )
        )

    logger.info(
        ("gpu_stage_complete document_id=%s stage=%s pages=%s"),
        request.document_id,
        _FINALIZATION_STAGE,
        total,
    )

    return FinalizeStageResponse(
        items=result,
    )
