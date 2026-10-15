# services/analysis-service/src/pdrd_analysis_service/application/limited_vision.py

"""Общая очередь VLM с чередованием заданий без изменения shared GPU runtime.

Лимит действует на один процесс Analysis Service. Все этапы используют один
экземпляр; запросы одного задания идут FIFO, задания получают слоты по кругу.
"""

import asyncio
import logging
from collections import OrderedDict, deque
from contextlib import suppress
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any

from pdrd_analysis_service.application.ports.vision_model import (
    StructuredVisionModel,
    VisionModelError,
)
from pdrd_analysis_service.application.vision_execution import (
    AnalysisCancelledError,
    VisionExecution,
    current_vision_execution,
)
from pdrd_analysis_service.domain.analysis import GenerationResult

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Waiter:
    """Слот очереди, отличающий ожидание от выданного разрешения."""

    key: str
    ready: asyncio.Future[None]
    granted: bool = False
    admitted_at: float = 0.0


class LimitedVisionModel:
    """Распределяет общий лимит по заданиям и проверяет отмену после ожидания."""

    def __init__(
        self, delegate: StructuredVisionModel, max_concurrent: int = 4
    ) -> None:
        """Создаёт очередь процесса; физические параметры GPU остаются внешними."""
        if max_concurrent < 1:
            raise ValueError("Лимит VLM должен быть положительным.")
        self.delegate = delegate
        self.max_concurrent = max_concurrent
        self._queues: OrderedDict[str, deque[_Waiter]] = OrderedDict()
        self._active: dict[str, int] = {}
        self._report_lock = asyncio.Lock()

    def _dispatch(self) -> None:
        """Выдаёт слоты, передвигая обслуженное задание в конец очереди."""
        while self._queues and sum(self._active.values()) < self.max_concurrent:
            key, queue = self._queues.popitem(last=False)
            waiter = queue.popleft()
            if queue:
                self._queues[key] = queue
            if waiter.ready.cancelled():
                continue
            waiter.granted = True
            waiter.admitted_at = perf_counter()
            self._active[key] = self._active.get(key, 0) + 1
            waiter.ready.set_result(None)

    def _release(self, waiter: _Waiter) -> None:
        """Удаляет отменённое ожидание или освобождает выданный слот без утечки."""
        if waiter.granted:
            count = self._active[waiter.key] - 1
            if count:
                self._active[waiter.key] = count
            else:
                del self._active[waiter.key]
        else:
            queue = self._queues.get(waiter.key)
            if queue is not None:
                queue.remove(waiter)
                if not queue:
                    del self._queues[waiter.key]
            waiter.ready.cancel()
        self._dispatch()

    async def _report(self, execution: VisionExecution | None) -> None:
        """Передаёт актуальные счётчики последовательно, без устаревших снимков."""
        if execution is None or execution.report_gpu is None:
            return
        async with self._report_lock:
            waiting = len(self._queues.get(execution.job_key, ()))
            running = self._active.get(execution.job_key, 0)
            if await execution.report_gpu(waiting, running):
                raise AnalysisCancelledError("Анализ отменён в очереди модели.")

    async def generate_json(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        num_predict: int,
        seed: int,
        stage: str,
        image_bytes: bytes | None = None,
    ) -> GenerationResult:
        """Измеряет ожидание отдельно от провайдера, не запуская отменённую работу.

        Уже выполняющийся inference завершается безопасно. Отмена ожидания либо
        ошибка освобождает слот; новое обращение отменённого задания запрещено.
        """
        execution = current_vision_execution.get()
        if execution is not None:
            await execution.checkpoint()
        waiter = _Waiter(
            execution.job_key if execution is not None else "unscoped",
            asyncio.get_running_loop().create_future(),
        )
        started = perf_counter()
        self._queues.setdefault(waiter.key, deque()).append(waiter)
        self._dispatch()
        try:
            await self._report(execution)
            while not waiter.ready.done():
                await asyncio.wait((waiter.ready,), timeout=1.0)
                if execution is not None:
                    await execution.checkpoint()
                await self._report(execution)
            await waiter.ready
            if execution is not None:
                await execution.checkpoint()
            await self._report(execution)
            wait_ms = (waiter.admitted_at - started) * 1000
            generated_at = perf_counter()
            provider_attempts = None
            try:
                result = await self.delegate.generate_json(
                    prompt=prompt,
                    schema=schema,
                    num_predict=num_predict,
                    seed=seed,
                    stage=stage,
                    image_bytes=image_bytes,
                )
                provider_attempts = getattr(
                    getattr(result, "metrics", None), "attempt", None
                )
            except VisionModelError as error:
                provider_attempts = (
                    error.metrics.attempt if error.metrics is not None else None
                )
                if error.metrics is not None:
                    error.metrics = replace(error.metrics, slot_wait_ms=wait_ms)
                raise
            finally:
                logger.info(
                    "vlm_slot_timing job=%s stage=%s slot_wait_ms=%.2f provider_ms=%.2f provider_attempts=%s",
                    waiter.key,
                    stage,
                    wait_ms,
                    (perf_counter() - generated_at) * 1000,
                    provider_attempts,
                )
            if isinstance(result, GenerationResult):
                result = replace(
                    result, metrics=replace(result.metrics, slot_wait_ms=wait_ms)
                )
            return result
        finally:
            self._release(waiter)
            # Завершающий heartbeat не заменяет успешный ответ или исходную ошибку.
            with suppress(AnalysisCancelledError):
                await self._report(execution)

    async def is_ready(self) -> bool:
        """Проверяет логический runtime без занятия inference слота."""
        return await self.delegate.is_ready()
