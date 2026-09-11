# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/messaging/tasks.py

"""Celery tasks Knowledge indexing."""

import asyncio
import logging
import threading
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
    touch_technical_assignment_indexing,
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


def _technical_assignment_heartbeat_loop(
    *,
    technical_assignment_id: UUID,
    celery_task_id: str,
    stop_event: threading.Event,
    interval_seconds: int,
) -> None:
    """Поддерживает DB heartbeat активного T-indexing."""
    while not stop_event.wait(
        interval_seconds,
    ):
        try:
            asyncio.run(
                touch_technical_assignment_indexing(
                    technical_assignment_id=technical_assignment_id,
                )
            )

        except Exception as error:
            LOGGER.warning(
                "technical_assignment_heartbeat_failed id=%s celery_task_id=%s "
                "error_type=%s error=%s",
                technical_assignment_id,
                celery_task_id,
                type(error).__name__,
                error,
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
    """Индексирует одно ТЗ с bounded retry и heartbeat."""
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
    retry_number = int(
        task.request.retries,
    )
    allow_retry = retry_number < max_retries
    celery_task_id = str(
        task.request.id or "unknown",
    )

    LOGGER.info(
        "technical_assignment_index_started id=%s celery_task_id=%s "
        "attempt=%s max_retries=%s state=indexing",
        parsed_id,
        celery_task_id,
        retry_number + 1,
        max_retries,
    )

    stop_event = threading.Event()

    heartbeat_thread = threading.Thread(
        target=_technical_assignment_heartbeat_loop,
        kwargs={
            "technical_assignment_id": parsed_id,
            "celery_task_id": celery_task_id,
            "stop_event": stop_event,
            "interval_seconds": (
                settings.technical_assignment_queue.heartbeat_interval_seconds
            ),
        },
        name=f"technical-assignment-heartbeat-{parsed_id}",
        daemon=True,
    )

    heartbeat_thread.start()

    try:
        assignment = asyncio.run(
            execute_technical_assignment_indexing(
                technical_assignment_id=parsed_id,
                allow_retry=allow_retry,
            )
        )

    except TechnicalAssignmentRetryableIndexingError as error:
        LOGGER.warning(
            "technical_assignment_index_retry id=%s celery_task_id=%s "
            "attempt=%s error_type=%s error=%s",
            parsed_id,
            celery_task_id,
            retry_number + 1,
            type(error).__name__,
            error,
        )

        raise task.retry(
            exc=error,
            countdown=(settings.technical_assignment.retry_delay_seconds),
            max_retries=max_retries,
        ) from error

    except Exception:
        LOGGER.exception(
            "technical_assignment_index_failed id=%s celery_task_id=%s attempt=%s",
            parsed_id,
            celery_task_id,
            retry_number + 1,
        )

        raise

    finally:
        stop_event.set()
        heartbeat_thread.join(
            timeout=5.0,
        )

    LOGGER.info(
        "technical_assignment_index_completed id=%s celery_task_id=%s "
        "attempt=%s state=%s",
        parsed_id,
        celery_task_id,
        retry_number + 1,
        assignment.index_status.value,
    )
