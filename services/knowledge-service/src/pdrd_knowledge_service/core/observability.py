# services/knowledge-service/src/pdrd_knowledge_service/core/observability.py

"""Измерение значимых асинхронных операций Knowledge без содержимого документов и ключей.

Декоратор измеряет всю операцию монотонными часами и пишет одно событие,
не дублируя traceback. CLI настраивает INFO сам; HTTP включает свои логгеры.
"""

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from time import perf_counter
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def configure_experience_logging() -> None:
    """Подключает только логгеры E, сохраняя настройки Uvicorn и других операций."""
    for module in ("index_experience", "trusted_experience"):
        logger = logging.getLogger(
            f"pdrd_knowledge_service.application.use_cases.{module}"
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
