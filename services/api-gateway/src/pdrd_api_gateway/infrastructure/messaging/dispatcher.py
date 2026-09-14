# services/api-gateway/src/pdrd_api_gateway/infrastructure/messaging/dispatcher.py

"""Фоновый процесс transactional outbox dispatcher и reconciliation."""

import asyncio
import logging
from functools import partial
from time import monotonic

from pdrd_api_gateway.application.use_cases.dispatch_outbox import (
    DispatchOutbox,
)
from pdrd_api_gateway.application.use_cases.recover_stale_analysis_jobs import (
    RecoverStaleAnalysisJobs,
)
from pdrd_api_gateway.core.settings import get_settings
from pdrd_api_gateway.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_api_gateway.infrastructure.database.unit_of_work import (
    SqlAlchemyUnitOfWork,
)
from pdrd_api_gateway.infrastructure.messaging.celery_app import (
    celery_app,
)
from pdrd_api_gateway.infrastructure.messaging.publisher import (
    CeleryOutboxPublisher,
)

LOGGER = logging.getLogger(
    __name__,
)


async def run_dispatcher() -> None:
    """Публикует outbox и периодически восстанавливает stale analysis jobs."""
    settings = get_settings()

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    unit_of_work_factory = partial(
        SqlAlchemyUnitOfWork,
        session_factory,
    )

    publisher = CeleryOutboxPublisher(
        celery_app=celery_app,
        broker_settings=settings.broker,
    )

    use_case = DispatchOutbox(
        unit_of_work_factory=unit_of_work_factory,
        publisher=publisher,
    )

    recovery = RecoverStaleAnalysisJobs(
        unit_of_work_factory=unit_of_work_factory,
        max_runtime_seconds=settings.lifecycle.max_runtime_seconds,
        max_attempts=settings.lifecycle.max_attempts,
        stale_processing_seconds=settings.lifecycle.stale_processing_seconds,
    )

    next_recovery_at = 0.0

    try:
        while True:
            now = monotonic()

            if now >= next_recovery_at:
                recovery_report = await recovery.execute(
                    limit=settings.lifecycle.recovery_batch_size,
                )

                if recovery_report.selected:
                    LOGGER.info(
                        "analysis_recovery selected=%s requeued=%s failed=%s",
                        recovery_report.selected,
                        recovery_report.requeued,
                        recovery_report.failed,
                    )

                next_recovery_at = now + settings.lifecycle.recovery_interval_seconds

            report = await use_case.execute(
                limit=settings.outbox.batch_size,
            )

            if report.published or report.failed:
                LOGGER.info(
                    "outbox_dispatch selected=%s published=%s failed=%s",
                    report.selected,
                    report.published,
                    report.failed,
                )

            if report.selected == 0 or report.failed > 0:
                await asyncio.sleep(
                    settings.outbox.poll_interval_seconds,
                )
            else:
                await asyncio.sleep(0)
    finally:
        await engine.dispose()


def main() -> None:
    """Запускает отдельный outbox/reconciliation process."""
    logging.basicConfig(
        level=logging.INFO,
        format=("%(asctime)s %(levelname)s %(name)s %(message)s"),
    )

    asyncio.run(
        run_dispatcher(),
    )


if __name__ == "__main__":
    main()
