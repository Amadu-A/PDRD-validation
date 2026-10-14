# services/analysis-service/src/pdrd_analysis_service/transport/http/stage_batch_routes.py

"""Document-scoped VLM HTTP API для больших PDF-документов."""

import asyncio
import logging
from collections.abc import (
    Awaitable,
    Callable,
    Sequence,
)
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from pdrd_analysis_service.application.vision_execution import (
    AnalysisCancelledError,
    vision_execution,
)
from pdrd_analysis_service.core.container import (
    ApplicationContainer,
)
from pdrd_analysis_service.transport.http.dependencies import (
    get_container,
)
from pdrd_analysis_service.transport.http.routes import (
    check_norms_controlled,
    finalize_findings,
    understand_page_controlled,
)
from pdrd_analysis_service.transport.http.schemas import (
    CheckNormsRequest,
    CheckNormsResponse,
    FinalizeResponse,
    UnderstandPageRequest,
    UnderstandPageResponse,
)
from pdrd_analysis_service.transport.http.stage_batch_schemas import (
    CheckNormsStageItemResponse,
    CheckNormsStageRequest,
    CheckNormsStageResponse,
    CheckTechnicalAssignmentStageItemResponse,
    CheckTechnicalAssignmentStageRequest,
    CheckTechnicalAssignmentStageResponse,
    FinalizeStageItemRequest,
    FinalizeStageItemResponse,
    FinalizeStageRequest,
    FinalizeStageResponse,
    TechnicalAssignmentStagePageRequest,
    UnderstandPagesStageItemResponse,
    UnderstandPagesStageRequest,
    UnderstandPagesStageResponse,
)
from pdrd_analysis_service.transport.http.technical_assignment_routes import (
    check_technical_assignment,
)
from pdrd_analysis_service.transport.http.technical_assignment_schemas import (
    CheckTechnicalAssignmentRequest,
    CheckTechnicalAssignmentResponse,
)
from pdrd_analysis_service.transport.http.vision_execution import stage_execution

router = APIRouter()

logger = logging.getLogger(
    "uvicorn.error",
)

_UNDERSTANDING_STAGE = "understanding_sheet"
_TECHNICAL_ASSIGNMENT_STAGE = "checking_technical_assignment"
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
            "Количество страниц VLM stage "
            f"({actual}) превышает configured limit "
            f"({maximum})."
        ),
    )


async def _is_stage_cancelled(
    *,
    container: ApplicationContainer,
    document_id: UUID,
    stage: str,
    current: int,
    total: int,
) -> bool:
    """Проверяет cancellation непосредственно перед новым VLM item."""
    logger.info(
        ("vlm_stage_item_start document_id=%s stage=%s current=%s total=%s"),
        document_id,
        stage,
        current,
        total,
    )

    probe = container.analysis_progress_probe

    if probe is None:
        return False

    cancelled = await probe.is_cancelled(
        document_id=document_id,
        stage=stage,
        current=current,
        total=total,
    )

    if cancelled:
        logger.info(
            ("vlm_stage_cancelled document_id=%s stage=%s before_item=%s total=%s"),
            document_id,
            stage,
            current,
            total,
        )

    return cancelled


async def _run_stage_items[ItemT, ResultT](
    *,
    container: ApplicationContainer,
    document_id: UUID,
    stage: str,
    items: Sequence[ItemT],
    process: Callable[
        [ItemT],
        Awaitable[ResultT],
    ],
) -> list[ResultT] | None:
    """Выполняет stage bounded-concurrently, сохраняя исходный порядок.

    Cancellation не прерывает уже запущенные inference.
    Новые items после обнаружения cancellation больше не стартуют.
    None означает штатную отмену всего этапа; частичный список не возвращается.
    """
    total = len(
        items,
    )

    if total == 0:
        return []

    concurrency = min(
        container.settings.pipeline.vlm_stage_concurrency,
        total,
    )

    results: list[ResultT | None] = [None] * total

    next_index = 0

    stop_event = asyncio.Event()

    state_lock = asyncio.Lock()

    first_error: Exception | None = None

    cancelled_before: int | None = None

    async def claim_next() -> tuple[int, ItemT] | None:
        nonlocal next_index

        async with state_lock:
            if stop_event.is_set() or next_index >= total:
                return None

            index = next_index

            next_index += 1

            return (
                index,
                items[index],
            )

    async def worker() -> None:
        nonlocal first_error
        nonlocal cancelled_before

        while True:
            claimed = await claim_next()

            if claimed is None:
                return

            index, item = claimed

            current = index + 1

            if await _is_stage_cancelled(
                container=container,
                document_id=document_id,
                stage=stage,
                current=current,
                total=total,
            ):
                async with state_lock:
                    if cancelled_before is None:
                        cancelled_before = current

                    stop_event.set()

                return

            try:
                with vision_execution(
                    stage_execution(
                        container.analysis_progress_probe,
                        document_id=document_id,
                        stage=stage,
                        current=current,
                        total=total,
                    )
                ):
                    result = await process(item)

            except asyncio.CancelledError:
                raise

            except Exception as error:
                async with state_lock:
                    if first_error is None or (
                        _is_cancellation_error(first_error)
                        and not _is_cancellation_error(error)
                    ):
                        first_error = error

                    stop_event.set()

                return

            results[index] = result

            logger.info(
                ("vlm_stage_item_complete document_id=%s stage=%s current=%s total=%s"),
                document_id,
                stage,
                current,
                total,
            )

    await asyncio.gather(
        *(
            worker()
            for _ in range(
                concurrency,
            )
        )
    )

    if first_error is not None:
        if _is_cancellation_error(first_error):
            return None
        raise first_error

    if cancelled_before is not None:
        return None

    if any(item is None for item in results):
        raise RuntimeError(
            "VLM stage завершился без части результатов.",
        )

    return [item for item in results if item is not None]


def _is_cancellation_error(error: Exception) -> bool:
    """Отличает штатную отмену от настоящей ошибки, которую отмена не должна скрыть."""
    return isinstance(error, AnalysisCancelledError) or (
        isinstance(error, HTTPException)
        and error.status_code == 409
        and isinstance(error.detail, dict)
        and error.detail.get("code") == "analysis_cancelled"
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
    """Понимает страницы с bounded concurrency shared-vlm."""
    _validate_stage_size(
        actual=len(
            request.items,
        ),
        maximum=container.settings.pipeline.max_stage_pages,
    )

    async def process(
        item: UnderstandPageRequest,
    ) -> UnderstandPageResponse:
        async def should_stop() -> bool:
            """Не запускает следующую порцию строк после отмены владельцем задания."""
            return await _is_stage_cancelled(
                container=container,
                document_id=request.document_id,
                stage=_UNDERSTANDING_STAGE,
                current=request.items.index(item) + 1,
                total=len(request.items),
            )

        return await understand_page_controlled(
            request=item,
            container=container,
            should_stop=should_stop
            if container.analysis_progress_probe is not None
            else None,
        )

    responses = await _run_stage_items(
        container=container,
        document_id=request.document_id,
        stage=_UNDERSTANDING_STAGE,
        items=request.items,
        process=process,
    )

    if responses is None:
        return UnderstandPagesStageResponse(items=[], cancelled=True)

    result = [
        UnderstandPagesStageItemResponse(
            page_number=item.page_number,
            result=response,
        )
        for item, response in zip(
            request.items,
            responses,
            strict=True,
        )
    ]

    logger.info(
        ("vlm_stage_complete document_id=%s stage=%s pages=%s concurrency=%s"),
        request.document_id,
        _UNDERSTANDING_STAGE,
        len(
            request.items,
        ),
        container.settings.pipeline.vlm_stage_concurrency,
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
    """Выполняет T-first с bounded concurrency shared-vlm."""
    _validate_stage_size(
        actual=len(
            request.items,
        ),
        maximum=container.settings.pipeline.max_stage_pages,
    )

    async def process(
        item: TechnicalAssignmentStagePageRequest,
    ) -> CheckTechnicalAssignmentResponse:
        return await check_technical_assignment(
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

    responses = await _run_stage_items(
        container=container,
        document_id=request.document_id,
        stage=_TECHNICAL_ASSIGNMENT_STAGE,
        items=request.items,
        process=process,
    )

    if responses is None:
        return CheckTechnicalAssignmentStageResponse(items=[], cancelled=True)

    result = [
        CheckTechnicalAssignmentStageItemResponse(
            page_number=item.page_number,
            result=response,
        )
        for item, response in zip(
            request.items,
            responses,
            strict=True,
        )
    ]

    logger.info(
        ("vlm_stage_complete document_id=%s stage=%s pages=%s concurrency=%s"),
        request.document_id,
        _TECHNICAL_ASSIGNMENT_STAGE,
        len(
            request.items,
        ),
        container.settings.pipeline.vlm_stage_concurrency,
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
    """Проверяет N/T/U с bounded concurrency shared-vlm."""
    _validate_stage_size(
        actual=len(
            request.items,
        ),
        maximum=container.settings.pipeline.max_stage_pages,
    )

    async def process(
        item: CheckNormsRequest,
    ) -> CheckNormsResponse:
        async def check_cancellation() -> None:
            """Отмена перед продолжением не запускает следующую GPU-порцию листа."""
            if await _is_stage_cancelled(
                container=container,
                document_id=request.document_id,
                stage=_CHECKING_STAGE,
                current=request.items.index(item) + 1,
                total=len(request.items),
            ):
                raise HTTPException(
                    409,
                    detail={
                        "code": "analysis_cancelled",
                        "message": "Анализ отменён между порциями нормативной проверки.",
                    },
                )

        return await check_norms_controlled(
            request=item,
            container=container,
            check_cancellation=check_cancellation
            if container.analysis_progress_probe is not None
            else None,
        )

    responses = await _run_stage_items(
        container=container,
        document_id=request.document_id,
        stage=_CHECKING_STAGE,
        items=request.items,
        process=process,
    )

    if responses is None:
        return CheckNormsStageResponse(items=[], cancelled=True)

    result = [
        CheckNormsStageItemResponse(
            page_number=item.page_number,
            result=response,
        )
        for item, response in zip(
            request.items,
            responses,
            strict=True,
        )
    ]

    logger.info(
        ("vlm_stage_complete document_id=%s stage=%s pages=%s concurrency=%s"),
        request.document_id,
        _CHECKING_STAGE,
        len(
            request.items,
        ),
        container.settings.pipeline.vlm_stage_concurrency,
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
    """Финализирует страницы с bounded concurrency shared-vlm."""
    _validate_stage_size(
        actual=len(
            request.items,
        ),
        maximum=container.settings.pipeline.max_stage_pages,
    )

    async def process(
        item: FinalizeStageItemRequest,
    ) -> FinalizeResponse:
        return await finalize_findings(
            request=item.request,
            container=container,
        )

    responses = await _run_stage_items(
        container=container,
        document_id=request.document_id,
        stage=_FINALIZATION_STAGE,
        items=request.items,
        process=process,
    )

    if responses is None:
        return FinalizeStageResponse(items=[], cancelled=True)

    result = [
        FinalizeStageItemResponse(
            page_number=item.page_number,
            result=response,
        )
        for item, response in zip(
            request.items,
            responses,
            strict=True,
        )
    ]

    logger.info(
        ("vlm_stage_complete document_id=%s stage=%s pages=%s concurrency=%s"),
        request.document_id,
        _FINALIZATION_STAGE,
        len(
            request.items,
        ),
        container.settings.pipeline.vlm_stage_concurrency,
    )

    return FinalizeStageResponse(
        items=result,
    )
