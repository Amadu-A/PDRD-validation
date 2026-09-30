# services/api-gateway/src/pdrd_api_gateway/core/observability.py

"""Единый timing-декоратор значимых операций без payload и повторных traceback.

Использует монотонный таймер, пишет одно событие на success/error и не
включает аргументы, тексты замечаний, actor, имена файлов или ключи.
"""

import inspect
import logging
from functools import wraps
from time import perf_counter


def configure_review_logging() -> None:
    """Включает один обработчик timing Review независимо от root-логгера Uvicorn."""
    for operation in ("manage_review", "get_reviewed_pdf", "manage_experience"):
        _configure_timing_logger(f"pdrd_api_gateway.application.use_cases.{operation}")


def _configure_timing_logger(name: str) -> None:
    """Настраивает один обработчик на операцию без изменения root-логгера."""
    logger = logging.getLogger(name)
    if not any(
        getattr(handler, "pdrd_review_timing", False) for handler in logger.handlers
    ):
        handler = logging.StreamHandler()
        handler.pdrd_review_timing = True
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def log_execution_time(*, operation: str):
    """Добавляет одинаковое измерение к синхронной или асинхронной операции."""

    def decorate(function):
        """Сохраняет сигнатуру/документацию и выбирает способ вызова функции."""
        logger = logging.getLogger(function.__module__)

        def finish(started, status):
            """Записывает только стабильные поля времени, без исходных аргументов."""
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

        if inspect.iscoroutinefunction(function):

            @wraps(function)
            async def asynchronous(*args, **kwargs):
                """Измеряет await, сохраняя исходный тип исключения."""
                started, status = perf_counter(), "error"
                try:
                    result = await function(*args, **kwargs)
                    status = "success"
                    return result
                finally:
                    finish(started, status)

            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            """Измеряет обычный вызов без перехвата или маскирования исключений."""
            started, status = perf_counter(), "error"
            try:
                result = function(*args, **kwargs)
                status = "success"
                return result
            finally:
                finish(started, status)

        return synchronous

    return decorate
