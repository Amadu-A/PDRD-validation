# services/api-gateway/src/pdrd_api_gateway/application/use_cases/get_reviewed_pdf.py

"""Экспорт утверждённого Review с проверкой источника и повторной проверкой перед выдачей."""

import hashlib
from dataclasses import dataclass, replace
from pathlib import PurePath
from uuid import UUID

from pdrd_api_gateway.application.ports.analysis_pdf_export import (
    AnalysisAnnotatedPdfDocument,
    AnalysisAnnotatedPdfRenderer,
)
from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactStorageError,
    AnalysisArtifactStore,
)
from pdrd_api_gateway.application.ports.review import (
    ReviewAccessPolicy,
    ReviewContextProvider,
    ReviewRequestError,
)
from pdrd_api_gateway.application.ports.reviewed_pdf import (
    ReviewedPdfCache,
    ReviewedPdfManifest,
    ReviewedPdfSource,
)
from pdrd_api_gateway.application.use_cases.get_analysis_job import GetAnalysisJob
from pdrd_api_gateway.application.use_cases.reviewed_pdf_payload import reviewed_payload
from pdrd_api_gateway.core.observability import log_execution_time

RENDERER_VERSION = "reviewed-v1"


@dataclass(frozen=True, slots=True)
class GetReviewedPdf:
    """Gateway проверяет доступ, Experience утверждение, Document Service рисует PDF."""

    contexts: ReviewContextProvider
    access: ReviewAccessPolicy
    jobs: GetAnalysisJob
    source: ReviewedPdfSource
    artifacts: AnalysisArtifactStore
    renderer: AnalysisAnnotatedPdfRenderer
    cache: ReviewedPdfCache

    async def _original(self, manifest: ReviewedPdfManifest) -> bytes:
        """Не позволяет подменить документ или исходный PDF после открытия Review."""
        job = await self.jobs.execute(job_id=manifest.job_id)
        if job is None or job.document_id != manifest.document_id:
            raise ReviewRequestError(409, "Исходный документ Review изменён.")
        if getattr(job, "source_artifacts_deleted_at", None) is not None:
            raise ReviewRequestError(
                410,
                "Исходный PDF удалён по сроку хранения 30 дней. Human Review сохранён.",
            )
        try:
            artifacts = await self.artifacts.load_request(
                document_id=manifest.document_id
            )
        except AnalysisArtifactStorageError as error:
            raise ReviewRequestError(
                503, "Исходный PDF временно недоступен."
            ) from error
        content = artifacts.pdf_content
        if (
            artifacts.submission.document_id != manifest.document_id
            or not content
            or not content.startswith(b"%PDF-")
            or hashlib.sha256(content).hexdigest() != manifest.source_sha256
        ):
            raise ReviewRequestError(
                409, "Исходный PDF не соответствует утверждённому Review."
            )
        return content

    @log_execution_time(operation="reviewed_pdf_export")
    async def execute(
        self, *, job_id: UUID, expected_revision: int, actor: str | None = None
    ) -> AnalysisAnnotatedPdfDocument:
        """Повторная проверка обязательна также при попадании в кеш."""
        context = self.contexts.resolve(job_id=job_id, operation="export")
        if actor is not None:
            context = replace(context, actor=actor)
        if context.job_id != job_id or context.operation != "export":
            raise ReviewRequestError(
                403, "Серверный контекст не соответствует экспорту."
            )
        await self.access.require(context)
        manifest = await self.source.load(context=context)
        if manifest.job_id != job_id or manifest.revision != expected_revision:
            raise ReviewRequestError(
                409, "Review изменён. Загрузите актуальную редакцию."
            )
        original = await self._original(manifest)
        key = hashlib.sha256(
            f"{RENDERER_VERSION}:{manifest.source_sha256}:{manifest.revision}:{manifest.digest}".encode()
        ).hexdigest()
        try:
            content = await self.cache.load(job_id=job_id, key=key)
            if content is None:
                annotations, report = reviewed_payload(manifest)
                content = await self.renderer.render(
                    pdf_content=original,
                    file_name=manifest.source_filename,
                    annotations=annotations,
                    report=report,
                )
                if not content.startswith(b"%PDF-"):
                    raise RuntimeError("Результат не является PDF.")
                await self.cache.save(job_id=job_id, key=key, content=content)
        except (OSError, RuntimeError) as error:
            raise ReviewRequestError(
                503, "Не удалось сформировать итоговый PDF. Повторите скачивание."
            ) from error
        current = await self.source.load(context=context)
        await self.access.require(context)
        if current != manifest:
            raise ReviewRequestError(
                409, "Review или подтверждённые области изменены во время экспорта."
            )
        await self._original(current)
        name = PurePath(manifest.source_filename.replace("\\", "/")).stem
        return AnalysisAnnotatedPdfDocument(
            content=content, file_name=f"{name}_reviewed.pdf"
        )
