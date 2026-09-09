# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/database/outbox_repository.py

"""SQLAlchemy repository transactional outbox Knowledge Service."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pdrd_knowledge_service.domain.normative_outbox import (
    NormativeOutboxMessage,
)
from pdrd_knowledge_service.infrastructure.database.outbox_model import (
    NormativeOutboxMessageModel,
)


class SqlAlchemyNormativeOutboxRepository:
    """PostgreSQL repository normative outbox."""

    def __init__(
        self,
        session: AsyncSession,
    ) -> None:
        """Сохраняет transaction session."""
        self._session = session

    async def add(
        self,
        message: NormativeOutboxMessage,
    ) -> None:
        """Добавляет outbox message и flush-ит transaction."""
        self._session.add(
            NormativeOutboxMessageModel(
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
    ) -> list[NormativeOutboxMessage]:
        """Блокирует и возвращает pending сообщения в FIFO порядке."""
        result = await self._session.scalars(
            select(
                NormativeOutboxMessageModel,
            )
            .where(
                NormativeOutboxMessageModel.published_at.is_(
                    None,
                ),
            )
            .order_by(
                NormativeOutboxMessageModel.created_at,
                NormativeOutboxMessageModel.id,
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
        message: NormativeOutboxMessage,
    ) -> None:
        """Обновляет состояние persisted outbox message."""
        model = await self._session.get(
            NormativeOutboxMessageModel,
            message.message_id,
        )

        if model is None:
            raise LookupError(
                f"Normative outbox message {message.message_id} not found.",
            )

        model.aggregate_id = message.aggregate_id
        model.event_type = message.event_type
        model.payload = message.payload
        model.attempt_count = message.attempt_count
        model.last_error = message.last_error
        model.created_at = message.created_at
        model.published_at = message.published_at

    @staticmethod
    def _to_domain(
        model: NormativeOutboxMessageModel,
    ) -> NormativeOutboxMessage:
        """Преобразует ORM model в domain message."""
        return NormativeOutboxMessage(
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
