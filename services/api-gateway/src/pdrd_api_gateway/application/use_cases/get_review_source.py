# services/api-gateway/src/pdrd_api_gateway/application/use_cases/get_review_source.py

"""Собирает артефакты текущего задания для Experience через порты Gateway.

Оригиналы, документ, статус и хеш не принимаются из браузера.
"""

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pdrd_api_gateway.application.ports.artifacts import AnalysisArtifactStore
from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.application.use_cases.get_analysis_job import GetAnalysisJob
from pdrd_api_gateway.application.use_cases.get_analysis_visualization import (
    GetAnalysisVisualization,
)
from pdrd_api_gateway.domain.analysis_job import AnalysisJobStatus


@dataclass(frozen=True, slots=True)
class ReviewSource:
    """Оригинал и результаты одного завершённого серверного задания."""

    job_id: UUID
    document_id: UUID
    source_filename: str
    source_sha256: str
    pdf_content: bytes
    result: dict[str, Any]
    visualization: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GetReviewSource:
    """Соединяет действующие источники анализа без копии его пайплайна."""

    jobs: GetAnalysisJob
    artifacts: AnalysisArtifactStore
    visualizations: GetAnalysisVisualization

    async def execute(self, *, job_id: UUID) -> ReviewSource:
        """Отклоняет чужой документ, незавершённый анализ и CAD без исходного PDF."""
        job = await self.jobs.execute(job_id=job_id)
        if job is None:
            raise ReviewRequestError(404, "Задание не найдено.")
        if job.status is not AnalysisJobStatus.COMPLETED or job.document_id is None:
            raise ReviewRequestError(409, "Анализ ещё не завершён.")
        artifacts = await self.artifacts.load_request(document_id=job.document_id)
        if artifacts.submission.document_id != job.document_id:
            raise ReviewRequestError(422, "Исходный документ не соответствует заданию.")
        if artifacts.pdf_content is None:
            raise ReviewRequestError(
                422, "Human Review этого этапа требует исходный PDF."
            )
        result = await self.artifacts.load_result(document_id=job.document_id)
        if result is None:
            raise ReviewRequestError(409, "Результат анализа ещё не сохранён.")
        visualization = await self.visualizations.execute(job_id=job_id)
        return ReviewSource(
            job_id=job_id,
            document_id=job.document_id,
            source_filename=artifacts.submission.pdf_file_name or "document.pdf",
            source_sha256=hashlib.sha256(artifacts.pdf_content).hexdigest(),
            pdf_content=artifacts.pdf_content,
            result=result,
            visualization=visualization,
        )
