# services/api-gateway/src/pdrd_api_gateway/infrastructure/messaging/tasks.py

"""Celery tasks infrastructure-уровня API Gateway."""

import asyncio
import logging
import os
import socket
import threading
from uuid import UUID

from billiard.exceptions import SoftTimeLimitExceeded
from celery import Task

from pdrd_api_gateway.application.use_cases.execute_analysis_job import (
    AnalysisJobNotExecutableError,
    AnalysisTransientExecutionError,
)
from pdrd_api_gateway.core.settings import get_settings
from pdrd_api_gateway.infrastructure.messaging.celery_app import (
    celery_app,
)
from pdrd_api_gateway.infrastructure.messaging.worker_runtime import (
    execute_analysis_job,
    fail_analysis_job_from_worker,
    touch_analysis_job,
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


def _heartbeat_loop(
    *,
    job_id: UUID,
    celery_task_id: str,
    stop_event: threading.Event,
    interval_seconds: int,
) -> None:
    """Периодически продлевает DB heartbeat активного analysis job."""
    while not stop_event.wait(
        interval_seconds,
    ):
        try:
            asyncio.run(
                touch_analysis_job(
                    job_id=job_id,
                )
            )

        except Exception as error:
            LOGGER.warning(
                "analysis_job_heartbeat_failed analysis_id=%s celery_task_id=%s "
                "error_type=%s error=%s",
                job_id,
                celery_task_id,
                type(error).__name__,
                error,
            )


@celery_app.task(
    bind=True,
    name="pdrd.analysis.requested",
    ignore_result=True,
)
def analysis_requested(
    task: Task,
    job_id: str,
) -> None:
    """Выполняет analysis job с retry, heartbeat и hard safety deadline."""
    try:
        parsed_job_id = UUID(
            job_id,
        )
    except ValueError:
        LOGGER.exception(
            "analysis_job_invalid_id analysis_id=%s",
            job_id,
        )

        raise

    settings = get_settings()

    celery_task_id = str(
        task.request.id or "unknown",
    )

    delivery_info = task.request.delivery_info or {}
    redelivered = bool(
        delivery_info.get(
            "redelivered",
            False,
        )
    )

    retry_number = int(
        task.request.retries,
    )

    allow_retry = retry_number < settings.lifecycle.transient_max_retries

    LOGGER.info(
        "analysis_job_started analysis_id=%s celery_task_id=%s attempt=%s "
        "redelivered=%s state=processing",
        parsed_job_id,
        celery_task_id,
        retry_number + 1,
        redelivered,
    )

    stop_event = threading.Event()

    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        kwargs={
            "job_id": parsed_job_id,
            "celery_task_id": celery_task_id,
            "stop_event": stop_event,
            "interval_seconds": settings.lifecycle.heartbeat_interval_seconds,
        },
        name=f"analysis-heartbeat-{parsed_job_id}",
        daemon=True,
    )

    heartbeat_thread.start()

    try:
        result = asyncio.run(
            execute_analysis_job(
                job_id=parsed_job_id,
                allow_retry=allow_retry,
                redelivered=redelivered,
            )
        )

    except AnalysisJobNotExecutableError as error:
        LOGGER.info(
            "analysis_job_redelivery_skipped analysis_id=%s celery_task_id=%s "
            "attempt=%s state=terminal error_type=%s",
            parsed_job_id,
            celery_task_id,
            retry_number + 1,
            type(error).__name__,
        )

        return

    except AnalysisTransientExecutionError as error:
        LOGGER.warning(
            "analysis_job_retry analysis_id=%s celery_task_id=%s attempt=%s "
            "state=queued error_type=%s error=%s",
            parsed_job_id,
            celery_task_id,
            retry_number + 1,
            type(error).__name__,
            error,
        )

        raise task.retry(
            exc=error,
            countdown=settings.lifecycle.transient_retry_delay_seconds,
            max_retries=settings.lifecycle.transient_max_retries,
        ) from error

    except SoftTimeLimitExceeded:
        LOGGER.error(
            "analysis_job_soft_timeout analysis_id=%s celery_task_id=%s "
            "attempt=%s state=failed",
            parsed_job_id,
            celery_task_id,
            retry_number + 1,
        )

        try:
            asyncio.run(
                fail_analysis_job_from_worker(
                    job_id=parsed_job_id,
                    error_code="analysis_worker_soft_timeout",
                    error_message=(
                        "Celery soft time limit остановил analysis job до hard kill."
                    ),
                )
            )
        except Exception:
            LOGGER.exception(
                "analysis_job_soft_timeout_persist_failed "
                "analysis_id=%s celery_task_id=%s",
                parsed_job_id,
                celery_task_id,
            )

        raise

    except Exception:
        LOGGER.exception(
            "analysis_job_failed analysis_id=%s celery_task_id=%s attempt=%s",
            parsed_job_id,
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
        "analysis_job_completed analysis_id=%s celery_task_id=%s attempt=%s "
        "state=completed source_mode=%s findings_count=%s",
        parsed_job_id,
        celery_task_id,
        retry_number + 1,
        result.get(
            "source_mode",
        ),
        result.get(
            "findings_count",
        ),
    )
