# services/api-gateway/src/pdrd_api_gateway/application/use_cases/cleanup_document_contexts.py

"""Страховочная очистка старых D-индексов с проверкой состояния задания."""

import logging
from dataclasses import dataclass
from datetime import timedelta

from pdrd_api_gateway.application.ports.document_context import DocumentContextLifecycle
from pdrd_api_gateway.application.ports.persistence import UnitOfWorkFactory
from pdrd_api_gateway.domain.analysis_job import AnalysisJobStatus, utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CleanupDocumentContexts:
    """Никогда не удаляет контекст активного задания по одному только возрасту."""

    unit_of_work_factory: UnitOfWorkFactory
    lifecycle: DocumentContextLifecycle
    max_runtime_seconds: int

    async def execute(self, *, limit: int, cursor: str = "") -> str:
        """Удаляет старые индексы без задания или с терминальным статусом."""
        candidates, next_cursor = await self.lifecycle.stale(
            before=utc_now() - timedelta(seconds=self.max_runtime_seconds + 300),
            limit=limit,
            cursor=cursor,
        )
        for document_id in candidates:
            try:
                async with self.unit_of_work_factory() as unit_of_work:
                    job = (
                        await unit_of_work.analysis_jobs.get_by_document_id_for_update(
                            document_id
                        )
                    )
                    if job is not None and job.status in {
                        AnalysisJobStatus.PENDING,
                        AnalysisJobStatus.QUEUED,
                        AnalysisJobStatus.PROCESSING,
                    }:
                        continue
                    await self.lifecycle.cleanup(context_id=document_id)
            except Exception:
                logger.exception(
                    "document_context_orphan_cleanup_failed document_id=%s", document_id
                )
        return next_cursor
