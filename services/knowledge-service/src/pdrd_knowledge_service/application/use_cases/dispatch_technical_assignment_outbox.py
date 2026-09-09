# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/dispatch_technical_assignment_outbox.py

"""Публикация отдельного T transactional outbox."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
)

from pdrd_knowledge_service.application.ports.technical_assignment_messaging import (
    TechnicalAssignmentOutboxPublisher,
    TechnicalAssignmentOutboxPublishError,
)
from pdrd_knowledge_service.application.ports.technical_assignment_persistence import (
    TechnicalAssignmentUnitOfWorkFactory,
)

Clock = Callable[
    [],
    datetime,
]


def utc_now() -> datetime:
    """Возвращает UTC now."""
    return datetime.now(
        UTC,
    )


@dataclass(frozen=True, slots=True)
class TechnicalAssignmentDispatchReport:
    """Статистика прохода T-outbox."""

    selected: int

    published: int

    failed: int


@dataclass(frozen=True, slots=True)
class DispatchTechnicalAssignmentOutbox:
    """Публикует committed T-indexing events."""

    unit_of_work_factory: TechnicalAssignmentUnitOfWorkFactory

    publisher: TechnicalAssignmentOutboxPublisher

    clock: Clock = utc_now

    async def execute(
        self,
        *,
        limit: int,
    ) -> TechnicalAssignmentDispatchReport:
        """Обрабатывает pending events."""
        published = 0

        failed = 0

        async with self.unit_of_work_factory() as unit_of_work:
            messages = await unit_of_work.outbox.get_pending(
                limit=limit,
            )

            for message in messages:
                try:
                    await self.publisher.publish(
                        message,
                    )

                except TechnicalAssignmentOutboxPublishError as error:
                    message.mark_failed(
                        error_message=str(
                            error,
                        ),
                    )

                    await unit_of_work.outbox.update(
                        message,
                    )

                    failed += 1

                    continue

                message.mark_published(
                    published_at=self.clock(),
                )

                await unit_of_work.outbox.update(
                    message,
                )

                published += 1

            await unit_of_work.commit()

        return TechnicalAssignmentDispatchReport(
            selected=len(
                messages,
            ),
            published=published,
            failed=failed,
        )
