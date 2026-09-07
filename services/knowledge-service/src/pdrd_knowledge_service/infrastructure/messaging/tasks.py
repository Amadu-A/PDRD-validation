# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/messaging/tasks.py

"""Celery tasks Knowledge indexing."""

import asyncio
import logging
from uuid import UUID

from celery import Task

from pdrd_knowledge_service.application.use_cases.index_technical_assignment import (
    TechnicalAssignmentRetryableIndexingError,
)
from pdrd_knowledge_service.core.settings import (
    get_settings,
)
from pdrd_knowledge_service.infrastructure.messaging.celery_app import (
    celery_app,
)
from pdrd_knowledge_service.infrastructure.messaging.technical_assignment_worker_runtime import (
    execute_technical_assignment_indexing,
)
from pdrd_knowledge_service.infrastructure.messaging.worker_runtime import (
    execute_normative_indexing,
)

LOGGER = logging.getLogger(
    __name__,
)


@celery_app.task(
    name="pdrd.knowledge.normative.index",
    ignore_result=True,
)
def normative_index(
    document_id: str,
) -> None:
    """Индексирует один queued managed документ."""
    try:
        parsed_document_id = UUID(
            document_id,
        )

    except ValueError:
        LOGGER.exception(
            "normative_index_invalid_document_id document_id=%s",
            document_id,
        )

        raise

    LOGGER.info(
        "normative_index_started document_id=%s",
        parsed_document_id,
    )

    try:
        document = asyncio.run(
            execute_normative_indexing(
                document_id=parsed_document_id,
            )
        )

    except Exception:
        LOGGER.exception(
            "normative_index_failed document_id=%s",
            parsed_document_id,
        )

        raise

    LOGGER.info(
        "normative_index_completed document_id=%s status=%s",
        parsed_document_id,
        document.index_status.value,
    )


@celery_app.task(
    bind=True,
    name=("pdrd.knowledge.technical_assignment.index"),
    ignore_result=True,
)
def technical_assignment_index(
    task: Task,
    technical_assignment_id: str,
) -> None:
    """Индексирует одно ТЗ с bounded retry."""
    try:
        parsed_id = UUID(
            technical_assignment_id,
        )

    except ValueError:
        LOGGER.exception(
            "technical_assignment_invalid_id id=%s",
            technical_assignment_id,
        )

        raise

    settings = get_settings()

    max_retries = settings.technical_assignment.max_retries

    allow_retry = task.request.retries < max_retries

    LOGGER.info(
        "technical_assignment_index_started id=%s retry=%s/%s",
        parsed_id,
        task.request.retries,
        max_retries,
    )

    try:
        assignment = asyncio.run(
            execute_technical_assignment_indexing(
                technical_assignment_id=parsed_id,
                allow_retry=allow_retry,
            )
        )

    except TechnicalAssignmentRetryableIndexingError as error:
        LOGGER.warning(
            "technical_assignment_index_retry id=%s retry=%s error=%s",
            parsed_id,
            task.request.retries,
            error,
        )

        raise task.retry(
            exc=error,
            countdown=(settings.technical_assignment.retry_delay_seconds),
            max_retries=max_retries,
        ) from error

    except Exception:
        LOGGER.exception(
            "technical_assignment_index_failed id=%s",
            parsed_id,
        )

        raise

    LOGGER.info(
        "technical_assignment_index_completed id=%s status=%s",
        parsed_id,
        assignment.index_status.value,
    )
