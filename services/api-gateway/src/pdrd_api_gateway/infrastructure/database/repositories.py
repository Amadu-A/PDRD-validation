# services/api-gateway/src/pdrd_api_gateway/infrastructure/database/repositories.py

"""SQLAlchemy repositories API Gateway."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
)
from pdrd_api_gateway.infrastructure.database.models import (
    AnalysisJobModel,
)


class SqlAlchemyAnalysisJobRepository:
    """SQLAlchemy-реализация repository заданий анализа."""

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        """Сохраняет session текущего Unit of Work."""
        self._session = session

    async def add(
        self,
        job: AnalysisJob,
    ) -> None:
        """Добавляет domain entity в текущую transaction."""
        self._session.add(
            AnalysisJobModel(
                id=job.id,
                status=job.status.value,
                attempt_count=job.attempt_count,
                error_code=job.error_code,
                error_message=job.error_message,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
        )

    async def get(
        self,
        job_id: UUID,
    ) -> AnalysisJob | None:
        """Загружает задание и преобразует ORM model в domain entity."""
        model = await self._session.get(
            AnalysisJobModel,
            job_id,
        )

        if model is None:
            return None

        return AnalysisJob(
            id=model.id,
            status=AnalysisJobStatus(
                model.status,
            ),
            attempt_count=model.attempt_count,
            error_code=model.error_code,
            error_message=model.error_message,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
