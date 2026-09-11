# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/technical_assignment_persistence.py

"""SQLAlchemy repositories и Unit of Work ТЗ."""

from datetime import datetime
from types import TracebackType
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from pdrd_knowledge_service.domain.technical_assignment import (
    TechnicalAssignment,
    TechnicalAssignmentIndexStatus,
)
from pdrd_knowledge_service.domain.technical_assignment_outbox import (
    TechnicalAssignmentOutboxMessage,
)
from pdrd_knowledge_service.infrastructure.database.repositories import (
    SqlAlchemyNormativeSectionRepository,
)
from pdrd_knowledge_service.infrastructure.database.technical_assignment_models import (
    TechnicalAssignmentModel,
    TechnicalAssignmentOutboxMessageModel,
)


class SqlAlchemyTechnicalAssignmentRepository:
    """PostgreSQL repository T lifecycle."""

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        """Сохраняет session."""
        self._session = session

    async def add(
        self,
        assignment: TechnicalAssignment,
    ) -> None:
        """Добавляет metadata ТЗ."""
        self._session.add(
            self._to_model(
                assignment,
            )
        )

        await self._session.flush()

    async def get(
        self,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignment | None:
        """Возвращает ТЗ по UUID."""
        model = await self._session.get(
            TechnicalAssignmentModel,
            technical_assignment_id,
        )

        return (
            self._to_domain(
                model,
            )
            if model is not None
            else None
        )

    async def get_for_update(
        self,
        technical_assignment_id: UUID,
    ) -> TechnicalAssignment | None:
        """Возвращает row с FOR UPDATE."""
        result = await self._session.scalar(
            select(
                TechnicalAssignmentModel,
            )
            .where(TechnicalAssignmentModel.id == technical_assignment_id)
            .with_for_update()
        )

        return (
            self._to_domain(
                result,
            )
            if result is not None
            else None
        )

    async def get_recoverable(
        self,
        *,
        stale_indexing_before: datetime,
        deadline_before: datetime,
        limit: int,
    ) -> list[TechnicalAssignment]:
        """Блокирует stale INDEXING и просроченные QUEUED/INDEXING ТЗ."""
        result = await self._session.scalars(
            select(
                TechnicalAssignmentModel,
            )
            .where(
                or_(
                    and_(
                        TechnicalAssignmentModel.index_status
                        == TechnicalAssignmentIndexStatus.INDEXING.value,
                        TechnicalAssignmentModel.updated_at < stale_indexing_before,
                    ),
                    and_(
                        TechnicalAssignmentModel.index_status.in_(
                            (
                                TechnicalAssignmentIndexStatus.QUEUED.value,
                                TechnicalAssignmentIndexStatus.INDEXING.value,
                            )
                        ),
                        TechnicalAssignmentModel.created_at < deadline_before,
                    ),
                )
            )
            .order_by(
                TechnicalAssignmentModel.created_at,
                TechnicalAssignmentModel.id,
            )
            .limit(
                limit,
            )
            .with_for_update(
                skip_locked=True,
            )
        )

        return [
            self._to_domain(
                model,
            )
            for model in result.all()
        ]

    async def list_all(
        self,
    ) -> list[TechnicalAssignment]:
        """Возвращает persisted ТЗ в стабильном порядке."""
        result = await self._session.scalars(
            select(
                TechnicalAssignmentModel,
            ).order_by(
                TechnicalAssignmentModel.created_at,
                TechnicalAssignmentModel.id,
            )
        )

        return [
            self._to_domain(
                model,
            )
            for model in result.all()
        ]

    async def update(
        self,
        assignment: TechnicalAssignment,
    ) -> None:
        """Обновляет lifecycle."""
        model = await self._session.get(
            TechnicalAssignmentModel,
            assignment.technical_assignment_id,
        )

        if model is None:
            raise LookupError(
                f"Technical assignment {assignment.technical_assignment_id} not found.",
            )

        model.analysis_document_id = assignment.analysis_document_id
        model.section_id = assignment.section_id
        model.original_name = assignment.original_name
        model.mime_type = assignment.mime_type
        model.size_bytes = assignment.size_bytes
        model.sha256 = assignment.sha256
        model.index_status = assignment.index_status.value
        model.index_error = assignment.index_error
        model.indexed_at = assignment.indexed_at
        model.created_at = assignment.created_at
        model.updated_at = assignment.updated_at

    @staticmethod
    def _to_model(
        assignment: TechnicalAssignment,
    ) -> TechnicalAssignmentModel:
        """Преобразует Domain в ORM."""
        return TechnicalAssignmentModel(
            id=assignment.technical_assignment_id,
            analysis_document_id=(assignment.analysis_document_id),
            section_id=assignment.section_id,
            original_name=assignment.original_name,
            mime_type=assignment.mime_type,
            size_bytes=assignment.size_bytes,
            sha256=assignment.sha256,
            index_status=assignment.index_status.value,
            index_error=assignment.index_error,
            indexed_at=assignment.indexed_at,
            created_at=assignment.created_at,
            updated_at=assignment.updated_at,
        )

    @staticmethod
    def _to_domain(
        model: TechnicalAssignmentModel,
    ) -> TechnicalAssignment:
        """Преобразует ORM в Domain."""
        return TechnicalAssignment(
            technical_assignment_id=model.id,
            analysis_document_id=(model.analysis_document_id),
            section_id=model.section_id,
            original_name=model.original_name,
            mime_type=model.mime_type,
            size_bytes=model.size_bytes,
            sha256=model.sha256,
            index_status=(
                TechnicalAssignmentIndexStatus(
                    model.index_status,
                )
            ),
            index_error=model.index_error,
            indexed_at=model.indexed_at,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


class SqlAlchemyTechnicalAssignmentOutboxRepository:
    """PostgreSQL repository отдельного T-outbox."""

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        """Сохраняет session."""
        self._session = session

    async def add(
        self,
        message: TechnicalAssignmentOutboxMessage,
    ) -> None:
        """Добавляет outbox event."""
        self._session.add(
            TechnicalAssignmentOutboxMessageModel(
                id=message.message_id,
                aggregate_id=message.aggregate_id,
                event_type=message.event_type,
                payload=message.payload,
                attempt_count=message.attempt_count,
                last_error=message.last_error,
                created_at=message.created_at,
                published_at=message.published_at,
            )
        )

        await self._session.flush()

    async def get_pending(
        self,
        *,
        limit: int,
    ) -> list[TechnicalAssignmentOutboxMessage]:
        """Блокирует pending rows FIFO."""
        result = await self._session.scalars(
            select(
                TechnicalAssignmentOutboxMessageModel,
            )
            .where(
                TechnicalAssignmentOutboxMessageModel.published_at.is_(
                    None,
                ),
            )
            .order_by(
                TechnicalAssignmentOutboxMessageModel.created_at,
                TechnicalAssignmentOutboxMessageModel.id,
            )
            .limit(
                limit,
            )
            .with_for_update(
                skip_locked=True,
            )
        )

        return [
            self._to_domain(
                model,
            )
            for model in result.all()
        ]

    async def update(
        self,
        message: TechnicalAssignmentOutboxMessage,
    ) -> None:
        """Обновляет publication metadata."""
        model = await self._session.get(
            TechnicalAssignmentOutboxMessageModel,
            message.message_id,
        )

        if model is None:
            raise LookupError(
                f"Technical assignment outbox message {message.message_id} not found.",
            )

        model.attempt_count = message.attempt_count
        model.last_error = message.last_error
        model.published_at = message.published_at

    @staticmethod
    def _to_domain(
        model: TechnicalAssignmentOutboxMessageModel,
    ) -> TechnicalAssignmentOutboxMessage:
        """Преобразует ORM event в Domain."""
        return TechnicalAssignmentOutboxMessage(
            message_id=model.id,
            aggregate_id=model.aggregate_id,
            event_type=model.event_type,
            payload=dict(
                model.payload,
            ),
            attempt_count=model.attempt_count,
            last_error=model.last_error,
            created_at=model.created_at,
            published_at=model.published_at,
        )


class SqlAlchemyTechnicalAssignmentUnitOfWork:
    """Transaction boundary T lifecycle."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Создаёт независимую session."""
        self._session = session_factory()

        self.sections = SqlAlchemyNormativeSectionRepository(
            self._session,
        )

        self.assignments = SqlAlchemyTechnicalAssignmentRepository(
            self._session,
        )

        self.outbox = SqlAlchemyTechnicalAssignmentOutboxRepository(
            self._session,
        )

    async def __aenter__(
        self,
    ) -> "SqlAlchemyTechnicalAssignmentUnitOfWork":
        """Открывает UoW."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Rollback незавершённой transaction."""
        if self._session.in_transaction():
            await self._session.rollback()

        await self._session.close()

    async def commit(
        self,
    ) -> None:
        """Commit."""
        await self._session.commit()

    async def rollback(
        self,
    ) -> None:
        """Rollback."""
        await self._session.rollback()
