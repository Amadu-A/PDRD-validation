# services/api-gateway/src/pdrd_api_gateway/application/use_cases/manage_review.py

"""Проверяет серверный контекст и доступ к заданию перед операцией Review.

Точка проверки прав выделена отдельно: будущая авторизация заменит адаптер
контекста и политику доступа, не меняя модель или хранилище Experience.
"""

from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from pdrd_api_gateway.application.ports.review import (
    ReviewAccessPolicy,
    ReviewContext,
    ReviewContextProvider,
    ReviewOperation,
    ReviewRequestError,
    ReviewService,
)
from pdrd_api_gateway.application.use_cases.get_analysis_job import GetAnalysisJob
from pdrd_api_gateway.core.observability import log_execution_time
from pdrd_api_gateway.domain.analysis_job import AnalysisJobStatus


@dataclass(frozen=True, slots=True)
class CompletedJobReviewAccess:
    """Политика закрытого окружения: проверяет существование и статус задания.

    Проверка владельца будет добавлена с авторизацией. Текущий режим разрешён
    только серверному оператору выделенного закрытого фронта.
    """

    jobs: GetAnalysisJob

    async def require(self, context: ReviewContext) -> None:
        """Проверяет доступ при каждом чтении и каждой записи, включая восстановление."""
        if not context.actor.strip():
            raise ReviewRequestError(403, "Нет серверного контекста инженера.")
        job = await self.jobs.execute(job_id=context.job_id)
        if job is None:
            raise ReviewRequestError(404, "Задание не найдено.")
        if job.status is not AnalysisJobStatus.COMPLETED or job.document_id is None:
            raise ReviewRequestError(
                409, "Human Review доступен после завершения анализа."
            )


@dataclass(frozen=True, slots=True)
class ManageReview:
    """Оркестрирует доступ и вызов отдельного сервиса через application port."""

    contexts: ReviewContextProvider
    access: ReviewAccessPolicy
    service: ReviewService

    @log_execution_time(operation="review_request")
    async def execute(
        self,
        *,
        job_id: UUID,
        operation: ReviewOperation,
        command: dict[str, Any] | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Не принимает actor, роли, теги или серверные оригиналы из браузера."""
        context = self.contexts.resolve(job_id=job_id, operation=operation)
        if actor is not None:
            context = replace(context, actor=actor)
        if context.job_id != job_id or context.operation != operation:
            raise ReviewRequestError(
                403, "Серверный контекст не соответствует операции."
            )
        await self.access.require(context)
        return await self.service.execute(context=context, command=command)
