# services/experience-service/src/pdrd_experience_service/core/observability.py

"""Одно измерение значимой операции Experience без текстов, ключей и аргументов."""

import logging
from functools import wraps
from time import perf_counter


def configure_catalog_logging() -> None:
    """Настраивает собственные timing-логгеры один раз, не меняя root/Uvicorn."""
    for module in ("capture_experience", "catalog", "export_catalog", "review"):
        logger = logging.getLogger(
            f"pdrd_experience_service.application.use_cases.{module}"
        )
        if not any(
            getattr(handler, "experience_timing", False) for handler in logger.handlers
        ):
            handler = logging.StreamHandler()
            handler.experience_timing = True
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


def log_execution_time(*, operation: str):
    """Декоратор сохраняет сигнатуру и тип исключения; пишет одно событие в finally."""

    def decorate(function):
        """В application используются асинхронные порты и сценарии."""
        logger = logging.getLogger(function.__module__)

        @wraps(function)
        async def execute(*args, **kwargs):
            """Измеряет всю операцию, включая IO и проверку текущей редакции."""
            started, status = perf_counter(), "error"
            try:
                result = await function(*args, **kwargs)
                status = "success"
                return result
            finally:
                duration = round((perf_counter() - started) * 1000, 3)
                logger.info(
                    "event=operation_timing operation=%s duration_ms=%s status=%s",
                    operation,
                    duration,
                    status,
                    extra={
                        "event": "operation_timing",
                        "operation": operation,
                        "duration_ms": duration,
                        "status": status,
                    },
                )

        return execute

    return decorate
