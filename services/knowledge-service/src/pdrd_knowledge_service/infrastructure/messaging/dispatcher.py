# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/messaging/dispatcher.py

"""Фоновый dispatcher Knowledge outboxes и T reconciliation."""

import asyncio
import logging
from functools import partial
from time import monotonic

from pdrd_knowledge_service.application.use_cases.dispatch_normative_outbox import (
    DispatchNormativeOutbox,
)
from pdrd_knowledge_service.application.use_cases.dispatch_technical_assignment_outbox import (
    DispatchTechnicalAssignmentOutbox,
)
from pdrd_knowledge_service.application.use_cases.recover_stale_technical_assignments import (
    RecoverStaleTechnicalAssignments,
)
from pdrd_knowledge_service.core.settings import (
    get_settings,
)
from pdrd_knowledge_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_knowledge_service.infrastructure.database.technical_assignment_persistence import (
    SqlAlchemyTechnicalAssignmentUnitOfWork,
)
from pdrd_knowledge_service.infrastructure.database.unit_of_work import (
    SqlAlchemyNormativeCatalogUnitOfWork,
)
from pdrd_knowledge_service.infrastructure.messaging.celery_app import (
    celery_app,
)
from pdrd_knowledge_service.infrastructure.messaging.publisher import (
    CeleryNormativeOutboxPublisher,
    CeleryTechnicalAssignmentOutboxPublisher,
)

LOGGER = logging.getLogger(
    __name__,
)


async def run_dispatcher() -> None:
    """Публикует outbox events и восстанавливает stale T-indexing."""
    settings = get_settings()

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    normative_uow_factory = partial(
        SqlAlchemyNormativeCatalogUnitOfWork,
        session_factory,
    )

    technical_uow_factory = partial(
        SqlAlchemyTechnicalAssignmentUnitOfWork,
        session_factory,
    )

    normative_dispatcher = DispatchNormativeOutbox(
        unit_of_work_factory=normative_uow_factory,
        publisher=CeleryNormativeOutboxPublisher(
            celery_app=celery_app,
            broker_settings=settings.broker,
        ),
    )

    technical_dispatcher = DispatchTechnicalAssignmentOutbox(
        unit_of_work_factory=technical_uow_factory,
        publisher=(
            CeleryTechnicalAssignmentOutboxPublisher(
                celery_app=celery_app,
                queue_settings=(settings.technical_assignment_queue),
            )
        ),
    )

    recovery = RecoverStaleTechnicalAssignments(
        unit_of_work_factory=technical_uow_factory,
        max_runtime_seconds=(settings.technical_assignment_queue.max_runtime_seconds),
        stale_indexing_seconds=(
            settings.technical_assignment_queue.stale_indexing_seconds
        ),
    )

    next_recovery_at = 0.0

    try:
        while True:
            now = monotonic()

            if now >= next_recovery_at:
                recovery_report = await recovery.execute(
                    limit=(settings.technical_assignment_queue.recovery_batch_size),
                )

                if recovery_report.selected:
                    LOGGER.info(
                        "technical_assignment_recovery selected=%s requeued=%s failed=%s",
                        recovery_report.selected,
                        recovery_report.requeued,
                        recovery_report.failed,
                    )

                next_recovery_at = now + (
                    settings.technical_assignment_queue.recovery_interval_seconds
                )

            normative_report = await normative_dispatcher.execute(
                limit=settings.outbox.batch_size,
            )

            technical_report = await technical_dispatcher.execute(
                limit=settings.outbox.batch_size,
            )

            selected = normative_report.selected + technical_report.selected

            failed = normative_report.failed + technical_report.failed

            if (
                normative_report.published
                or normative_report.failed
                or technical_report.published
                or technical_report.failed
            ):
                LOGGER.info(
                    "knowledge_outbox_dispatch "
                    "normative_selected=%s "
                    "normative_published=%s "
                    "normative_failed=%s "
                    "technical_selected=%s "
                    "technical_published=%s "
                    "technical_failed=%s",
                    normative_report.selected,
                    normative_report.published,
                    normative_report.failed,
                    technical_report.selected,
                    technical_report.published,
                    technical_report.failed,
                )

            if selected == 0 or failed > 0:
                await asyncio.sleep(
                    settings.outbox.poll_interval_seconds,
                )

            else:
                await asyncio.sleep(
                    0,
                )

    finally:
        await engine.dispose()


def main() -> None:
    """Запускает Knowledge outbox/reconciliation process."""
    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s %(levelname)s %(name)s %(message)s"),
    )

    asyncio.run(
        run_dispatcher(),
    )


if __name__ == "__main__":
    main()
