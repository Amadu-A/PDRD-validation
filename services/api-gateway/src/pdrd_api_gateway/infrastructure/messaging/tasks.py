# services/api-gateway/src/pdrd_api_gateway/infrastructure/messaging/tasks.py

"""Celery tasks infrastructure-уровня API Gateway."""

import logging
import os
import socket

from pdrd_api_gateway.infrastructure.messaging.celery_app import (
    celery_app,
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
    """Подтверждает получение job worker-ом.

    Реальный вызов n8n orchestration будет добавлен после появления
    document-service, knowledge-service и analysis-service.
    """
    LOGGER.info(
        "analysis_job_received job_id=%s",
        job_id,
    )
