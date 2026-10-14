# services/analysis-service/src/pdrd_analysis_service/application/vision_execution.py

"""Контекст задания для общего VLM-порта: справедливость, отмена и очередь.

Контекст наследуется асинхронными задачами, не меняет интерфейс модели и не входит
в ключ точного кеша. HTTP-слой передаёт функции application-контракта.
"""

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


class AnalysisCancelledError(RuntimeError):
    """Ожидаемая отмена до очередного обращения к модели."""


@dataclass(frozen=True, slots=True)
class VisionExecution:
    """Связывает элемент этапа с заданием и его сигналом отмены."""

    job_key: str
    check_cancelled: Callable[[], Awaitable[bool]]
    report_gpu: Callable[[int, int], Awaitable[bool]] | None = None

    async def checkpoint(self) -> None:
        """Не допускает новый inference после сохранённой отмены."""
        if await self.check_cancelled():
            raise AnalysisCancelledError("Анализ отменён до следующего запроса модели.")


current_vision_execution: ContextVar[VisionExecution | None] = ContextVar(
    "vision_execution", default=None
)


@contextmanager
def vision_execution(execution: VisionExecution) -> Iterator[None]:
    """Устанавливает контекст элемента и восстанавливает его при ошибке."""
    token = current_vision_execution.set(execution)
    try:
        yield
    finally:
        current_vision_execution.reset(token)
