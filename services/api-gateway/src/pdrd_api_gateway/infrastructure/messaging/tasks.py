# services/api-gateway/src/pdrd_api_gateway/infrastructure/messaging/tasks.py

"""Celery tasks infrastructure-уровня API Gateway."""

import asyncio
import logging
import os
import socket
from uuid import UUID

from pdrd_api_gateway.infrastructure.messaging.celery_app import (
    celery_app,
)
from pdrd_api_gateway.infrastructure.messaging.worker_runtime import (
    execute_analysis_job,
)

LOGGER = logging.getLogger(
    __name__,
)


@celery_app.task(
    name="pdrd.queue.probe",
    ignore_result=False,
)
def queue_probe(
    probe_id: str,
) -> dict[str, str | int]:
    """Возвращает данные worker для integration проверки очереди."""
    return {
        "status": "ok",
        "probe_id": probe_id,
        "hostname": socket.gethostname(),
        "process_id": os.getpid(),
    }


@celery_app.task(
    name="pdrd.analysis.requested",
    ignore_result=True,
)
def analysis_requested(
    job_id: str,
) -> None:
    """Выполняет queued analysis job через n8n orchestration."""
    try:
        parsed_job_id = UUID(
            job_id,
        )
    except ValueError:
        LOGGER.exception(
            "analysis_job_invalid_id job_id=%s",
            job_id,
        )

        raise

    LOGGER.info(
        "analysis_job_started job_id=%s",
        parsed_job_id,
    )

    try:
        result = asyncio.run(
            execute_analysis_job(
                job_id=parsed_job_id,
            )
        )
    except Exception:
        LOGGER.exception(
            "analysis_job_failed job_id=%s",
            parsed_job_id,
        )

        raise

    LOGGER.info(
        "analysis_job_completed job_id=%s source_mode=%s findings_count=%s",
        parsed_job_id,
        result.get(
            "source_mode",
        ),
        result.get(
            "findings_count",
        ),
    )
