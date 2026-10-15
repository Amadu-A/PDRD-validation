# services/analysis-service/src/pdrd_analysis_service/transport/http/vision_execution.py

"""Связывает HTTP-этап с отменой и наблюдаемостью application-очереди VLM."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, Request

from pdrd_analysis_service.application.ports.analysis_progress import (
    AnalysisProgressProbe,
)
from pdrd_analysis_service.application.vision_execution import (
    VisionExecution,
    vision_execution,
)
from pdrd_analysis_service.core.container import ApplicationContainer
from pdrd_analysis_service.transport.http.dependencies import get_container


def stage_execution(
    probe: AnalysisProgressProbe | None,
    *,
    document_id: UUID,
    stage: str,
    current: int,
    total: int,
) -> VisionExecution:
    """Передаёт модели идентичность задания и функции существующего progress API."""

    async def cancelled() -> bool:
        """Читает durable отмену, не меняя текущий этап на название VLM-порции."""
        return bool(
            probe is not None
            and await probe.is_cancelled(
                document_id=document_id,
                stage=stage,
                current=current,
                total=total,
            )
        )

    async def report(waiting: int, running: int) -> bool:
        """Обновляет отдельные счётчики очереди и генерации конкретного задания."""
        reporter = getattr(probe, "report_gpu_state", None)
        if reporter is None:
            return False
        return await reporter(
            document_id=document_id,
            stage=stage,
            waiting=waiting,
            running=running,
        )

    return VisionExecution(str(document_id), cancelled, report)


async def page_execution_context(
    request: Request,
    container: Annotated[ApplicationContainer, Depends(get_container)],
    document_id: Annotated[
        UUID | None, Header(alias="X-PDRD-Analysis-Document-ID")
    ] = None,
) -> AsyncIterator[None]:
    """Объединяет PDF/CAD page-вызовы в то же задание, не меняя payload и ключ кеша."""
    stages = {
        "/internal/v1/pages/understand": "understanding_sheet",
        "/internal/v1/pages/check-norms": "checking_requirements",
        "/internal/v1/pages/check-technical-assignment": "checking_technical_assignment",
        "/internal/v1/findings/finalize": "finalizing_findings",
        "/internal/v1/findings/localize": "localization",
    }
    stage = stages.get(request.url.path)
    if document_id is None or stage is None:
        yield
        return
    if stage == "localization":

        async def finished_analysis() -> bool:
            """Локализация готового отчёта не меняет прогресс и сигнал отмены завершённого анализа."""
            return False

        with vision_execution(VisionExecution(str(document_id), finished_analysis)):
            yield
        return
    with vision_execution(
        stage_execution(
            container.analysis_progress_probe,
            document_id=document_id,
            stage=stage,
            current=1,
            total=1,
        )
    ):
        yield
