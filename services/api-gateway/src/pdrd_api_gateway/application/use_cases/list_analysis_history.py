# services/api-gateway/src/pdrd_api_gateway/application/use_cases/list_analysis_history.py

"""История собственных проверок без повторного анализа и создания Human Review."""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID

from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactStorageError,
    AnalysisArtifactStore,
)
from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCatalogManagementError,
    NormativeCatalogManager,
)
from pdrd_api_gateway.application.ports.persistence import UnitOfWorkFactory
from pdrd_api_gateway.application.ports.review import (
    ReviewContext,
    ReviewRequestError,
    ReviewService,
)
from pdrd_api_gateway.domain.analysis_history import AnalysisHistoryMetadata
from pdrd_api_gateway.domain.analysis_job import AnalysisJob, AnalysisJobStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ListAnalysisHistory:
    """Читает ограниченную страницу только для подтверждённого владельца."""

    unit_of_work_factory: UnitOfWorkFactory
    artifacts: AnalysisArtifactStore
    sections: NormativeCatalogManager | None = None
    reviews: ReviewService | None = None

    async def execute(
        self,
        *,
        owner_user_id: UUID,
        limit: int = 20,
        offset: int = 0,
        can_download_reviewed_pdf: bool = False,
    ) -> dict[str, object]:
        """Отдельные недоступные артефакты/Review не скрывают остальные задания."""
        if not isinstance(owner_user_id, UUID):
            raise ValueError("Не подтверждён владелец истории")
        if not 1 <= limit <= 20 or not 0 <= offset <= 100000:
            raise ValueError("Некорректная страница истории")
        started = perf_counter()
        try:
            async with self.unit_of_work_factory() as work:
                jobs = await work.analysis_jobs.list_by_owner(
                    owner_user_id=owner_user_id, limit=limit + 1, offset=offset
                )
            # Дополнительная защита от ошибочного адаптера; чужие артефакты не читаются.
            if any(job.owner_user_id != owner_user_id for job in jobs):
                raise ValueError("Нарушена принадлежность истории")
            names = {}
            if self.sections is not None and any(
                job.normative_snapshot for job in jobs[:limit]
            ):
                with suppress(NormativeCatalogManagementError, TimeoutError):
                    async with asyncio.timeout(5):
                        names = {
                            section.section_id: section.name
                            for section in await self.sections.list_sections()
                        }
            semaphore = asyncio.Semaphore(4)

            async def item(job: AnalysisJob) -> dict[str, object]:
                """Ограничивает параллельное чтение файлов и внутренние запросы."""
                async with semaphore:
                    return await self._item(job, names, can_download_reviewed_pdf)

            items = await asyncio.gather(*(item(job) for job in jobs[:limit]))
            return {
                "items": items,
                "offset": offset,
                "limit": limit,
                "has_more": len(jobs) > limit,
            }
        finally:
            logger.info(
                "analysis_history elapsed_seconds=%.3f", perf_counter() - started
            )

    async def _item(
        self, job: AnalysisJob, names: dict[UUID, str], can_download_reviewed_pdf: bool
    ) -> dict[str, object]:
        """Собирает ссылки на имеющиеся результаты; чтение Review не создаёт ревизий."""
        metadata = AnalysisHistoryMetadata()
        artifacts_available = False
        if job.document_id is not None:
            try:
                metadata = await self.artifacts.load_summary(
                    document_id=job.document_id
                )
                artifacts_available = metadata.result_available
            except AnalysisArtifactStorageError:
                pass
        review_status = "not_opened"
        review_revision = None
        if self.reviews is not None and job.status == AnalysisJobStatus.COMPLETED:
            try:
                async with asyncio.timeout(5):
                    review = await self.reviews.execute(
                        context=ReviewContext(
                            actor=f"user:{job.owner_user_id}",
                            job_id=job.id,
                            operation="read",
                        )
                    )
                review_revision = review["revision"]
                if (
                    isinstance(review_revision, bool)
                    or not isinstance(review_revision, int)
                    or review_revision < 1
                ):
                    raise TypeError("Некорректная ревизия Review")
                review_status = (
                    "approved"
                    if review.get("approved_revision") == review_revision
                    else "in_progress"
                )
            except ReviewRequestError as error:
                review_status = (
                    "not_opened" if error.status_code == 404 else "unavailable"
                )
            except (TimeoutError, KeyError, TypeError):
                review_status = "unavailable"
        base = f"/api/v1/analyses/{job.id}"
        pdf_url = None
        pdf_kind = None
        if (
            artifacts_available
            and metadata.pdf_available
            and job.status == AnalysisJobStatus.COMPLETED
        ):
            pdf_kind = (
                "reviewed"
                if review_status == "approved" and can_download_reviewed_pdf
                else "annotated"
            )
            pdf_url = (
                f"{base}/reviewed-pdf"
                if pdf_kind == "reviewed"
                else f"{base}/annotated-pdf"
            )
        section_id = (
            job.normative_snapshot.section_id if job.normative_snapshot else None
        )
        return {
            "job_id": str(job.id),
            "document_id": str(job.document_id) if job.document_id else None,
            "status": job.status.value,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "file_name": metadata.file_name,
            "source_mode": metadata.source_mode,
            "pages_count": metadata.pages_count,
            "findings_count": metadata.findings_count,
            "section_id": str(section_id) if section_id else None,
            "section_name": names.get(section_id),
            "review_status": review_status,
            "review_revision": review_revision,
            "result_available": artifacts_available,
            "open_url": f"/?job_id={job.id}",
            "pdf_url": pdf_url,
            "pdf_kind": pdf_kind,
        }
