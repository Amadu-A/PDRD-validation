# services/api-gateway/src/pdrd_api_gateway/infrastructure/messaging/tasks.py

"""Celery tasks infrastructure-уровня API Gateway."""

import os
import socket

from pdrd_api_gateway.infrastructure.messaging.celery_app import (
    celery_app,
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
