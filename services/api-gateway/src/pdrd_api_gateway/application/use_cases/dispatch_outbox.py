# services/api-gateway/src/pdrd_api_gateway/application/use_cases/dispatch_outbox.py

"""Use case надёжной публикации transactional outbox."""

from dataclasses import dataclass

from pdrd_api_gateway.application.ports.messaging import (
    OutboxPublisher,
    OutboxPublishError,
)
from pdrd_api_gateway.application.ports.persistence import (
    UnitOfWorkFactory,
)
from pdrd_api_gateway.domain.analysis_job import AnalysisJobStatus
from pdrd_api_gateway.domain.outbox import OutboxMessage


@dataclass(frozen=True, slots=True)
class DispatchReport:
    """Статистика одного прохода dispatcher."""

    selected: int
    published: int
    failed: int


@dataclass(frozen=True, slots=True)
class DispatchOutbox:
    """Публикует ожидающие сообщения transactional outbox."""

    unit_of_work_factory: UnitOfWorkFactory
    publisher: OutboxPublisher

    async def execute(
        self,
        *,
        limit: int,
    ) -> DispatchReport:
        """Обрабатывает одну ограниченную пачку сообщений."""
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
                except OutboxPublishError as error:
                    message.mark_failed(
                        error_message=str(error),
                    )

                    await unit_of_work.outbox.update(
                        message,
                    )

                    failed += 1
                    continue

                message.mark_published()

                await unit_of_work.outbox.update(
                    message,
                )

                if message.event_type == OutboxMessage.ANALYSIS_REQUESTED_EVENT:
                    job = await unit_of_work.analysis_jobs.get(
                        message.aggregate_id,
                    )

                    if job is not None and job.status is AnalysisJobStatus.PENDING:
                        job.mark_queued()

                        await unit_of_work.analysis_jobs.update(
                            job,
                        )

                published += 1

            await unit_of_work.commit()

        return DispatchReport(
            selected=len(messages),
            published=published,
            failed=failed,
        )
