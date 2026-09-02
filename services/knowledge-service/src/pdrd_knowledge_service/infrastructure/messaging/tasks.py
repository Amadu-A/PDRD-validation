# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/messaging/tasks.py

"""Celery tasks нормативной индексации Knowledge Service."""

import asyncio
import logging
from uuid import UUID

from pdrd_knowledge_service.infrastructure.messaging.celery_app import (
    celery_app,
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
    """Индексирует один queued нормативный документ."""
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
