# services/api-gateway/src/pdrd_api_gateway/infrastructure/database/repositories.py

"""SQLAlchemy repositories API Gateway."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
)
from pdrd_api_gateway.domain.outbox import OutboxMessage
from pdrd_api_gateway.infrastructure.database.models import (
    AnalysisJobModel,
    OutboxMessageModel,
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
                document_id=job.document_id,
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
        """Загружает job и преобразует ORM model в domain entity."""
        model = await self._session.get(
            AnalysisJobModel,
            job_id,
        )

        if model is None:
            return None

        return self._to_domain(
            model,
        )

    async def update(
        self,
        job: AnalysisJob,
    ) -> None:
        """Обновляет persistence model из domain entity."""
        model = await self._session.get(
            AnalysisJobModel,
            job.id,
        )

        if model is None:
            raise LookupError(
                f"Analysis job {job.id} not found.",
            )

        model.document_id = job.document_id
        model.status = job.status.value
        model.attempt_count = job.attempt_count
        model.error_code = job.error_code
        model.error_message = job.error_message
        model.updated_at = job.updated_at

    @staticmethod
    def _to_domain(
        model: AnalysisJobModel,
    ) -> AnalysisJob:
        """Преобразует SQLAlchemy model в domain entity."""
        return AnalysisJob(
            id=model.id,
            document_id=model.document_id,
            status=AnalysisJobStatus(
                model.status,
            ),
            attempt_count=model.attempt_count,
            error_code=model.error_code,
            error_message=model.error_message,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class SqlAlchemyOutboxRepository:
    """SQLAlchemy implementation transactional outbox."""

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        """Сохраняет transaction session."""
        self._session = session

    async def add(
        self,
        message: OutboxMessage,
    ) -> None:
        """Добавляет outbox message."""
        self._session.add(
            OutboxMessageModel(
                id=message.id,
                aggregate_id=message.aggregate_id,
                event_type=message.event_type,
                payload=message.payload,
                attempt_count=message.attempt_count,
                last_error=message.last_error,
                created_at=message.created_at,
                published_at=message.published_at,
            )
        )

    async def get_pending(
        self,
        *,
        limit: int,
    ) -> list[OutboxMessage]:
        """Блокирует и возвращает пачку pending messages."""
        statement = (
            select(
                OutboxMessageModel,
            )
            .where(
                OutboxMessageModel.published_at.is_(None),
            )
            .order_by(
                OutboxMessageModel.created_at,
            )
            .limit(limit)
            .with_for_update(
                skip_locked=True,
            )
        )

        result = await self._session.scalars(
            statement,
        )

        return [self._to_domain(model) for model in result.all()]

    async def update(
        self,
        message: OutboxMessage,
    ) -> None:
        """Обновляет состояние outbox message."""
        model = await self._session.get(
            OutboxMessageModel,
            message.id,
        )

        if model is None:
            raise LookupError(
                f"Outbox message {message.id} not found.",
            )

        model.attempt_count = message.attempt_count
        model.last_error = message.last_error
        model.published_at = message.published_at

    @staticmethod
    def _to_domain(
        model: OutboxMessageModel,
    ) -> OutboxMessage:
        """Преобразует ORM model в domain entity."""
        return OutboxMessage(
            id=model.id,
            aggregate_id=model.aggregate_id,
            event_type=model.event_type,
            payload=dict(model.payload),
            attempt_count=model.attempt_count,
            last_error=model.last_error,
            created_at=model.created_at,
            published_at=model.published_at,
        )
