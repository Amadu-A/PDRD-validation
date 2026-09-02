# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/dispatch_normative_outbox.py

"""Use case публикации transactional outbox Knowledge Service."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import (
    UTC,
    datetime,
)

from pdrd_knowledge_service.application.ports.messaging import (
    NormativeOutboxPublisher,
    NormativeOutboxPublishError,
)
from pdrd_knowledge_service.application.ports.persistence import (
    NormativeCatalogUnitOfWorkFactory,
)

Clock = Callable[
    [],
    datetime,
]


def utc_now() -> datetime:
    """Возвращает текущее UTC время."""
    return datetime.now(
        UTC,
    )


@dataclass(frozen=True, slots=True)
class DispatchReport:
    """Статистика одного прохода outbox dispatcher."""

    selected: int

    published: int

    failed: int


@dataclass(frozen=True, slots=True)
class DispatchNormativeOutbox:
    """Публикует pending normative outbox events."""

    unit_of_work_factory: NormativeCatalogUnitOfWorkFactory

    publisher: NormativeOutboxPublisher

    clock: Clock = utc_now

    async def execute(
        self,
        *,
        limit: int,
    ) -> DispatchReport:
        """Обрабатывает одну пачку outbox сообщений."""
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

                except NormativeOutboxPublishError as error:
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

        return DispatchReport(
            selected=len(
                messages,
            ),
            published=published,
            failed=failed,
        )
