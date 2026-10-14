# services/analysis-service/src/pdrd_analysis_service/core/observability.py

"""Измерение ключевых EQ операций без аргументов и содержимого документов."""

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from time import perf_counter
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def log_execution_time(
    *, operation: str
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Сохраняет контракт async-операции и тип исключения; не пишет аргументы/результат."""

    def decorate(function: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        """Привязывает стабильное имя операции к её собственному логгеру."""
        logger = logging.getLogger(function.__module__)

        @wraps(function)
        async def execute(*args: P.args, **kwargs: P.kwargs) -> R:
            """Измеряет IO и проверки; исключение обрабатывает вызывающий слой."""
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
