# services/knowledge-service/src/pdrd_knowledge_service/infrastructure/embedding/request_runtime.py

"""Ограниченный клиентский admission, retry и точный кеш embedding внутри задания.

Повторяется только неуспешная микропорция. Во время backoff нет ни admission-слота,
ни GPU lease. FIFO между микропорциями позволяет разным заданиям продвигаться параллельно.
"""

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from collections import Counter, OrderedDict, deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from pdrd_knowledge_service.application.embedding_execution import (
    EmbeddingExecution,
    current_embedding_execution,
    embedding_execution,
)
from pdrd_knowledge_service.application.ports.embedding import EmbeddingTemporaryError
from pdrd_knowledge_service.application.ports.multimodal_embedding import (
    MultimodalEmbeddingInput,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmbeddingRequestPolicy:
    """Общие ограниченные бюджеты операции; они не меняют параметры shared vLLM."""

    max_attempts: int = 5
    max_elapsed_seconds: float = 120
    backoff_seconds: float = 1
    max_backoff_seconds: float = 15
    max_batch_size: int = 4
    max_concurrency: int = 1
    max_waiters: int = 32
    cache_entries: int = 256
    cache_ttl_seconds: float = 3600


@dataclass(slots=True)
class _Slots:
    """Очередь одного event loop; сетевое ожидание не блокирует её изменение."""

    active: int = 0
    queue: deque = field(default_factory=deque)


class EmbeddingRequestRuntime:
    """Разделяет admission и кеш между text/image адаптерами одной модели."""

    def __init__(
        self,
        policy: EmbeddingRequestPolicy | None = None,
        *,
        shared_slot: Callable[[], AbstractAsyncContextManager[bool]] | None = None,
        stage: str = "embedding",
        document_id: str = "",
    ) -> None:
        """Принимает межпроцессный слот; без него действует локальный FIFO для тестов/CLI."""
        self.policy = policy or EmbeddingRequestPolicy()
        self.shared_slot = shared_slot
        self._stage, self._document_id = stage, document_id
        self._loops: WeakKeyDictionary = WeakKeyDictionary()
        self._cache: OrderedDict = OrderedDict()

    async def _checkpoint(
        self, deadline: float, *, force_control: bool = False
    ) -> None:
        """Ограничивает и локальное ожидание, и оставшийся lifecycle задания."""
        context = current_embedding_execution.get()
        if context is not None:
            await context.checkpoint(force_control=force_control)
        if time.monotonic() >= deadline:
            raise EmbeddingTemporaryError(
                "Исчерпан бюджет ожидания embedding.",
                reason_code="embedding_budget_exhausted",
            )

    async def _pause(
        self, seconds: float, deadline: float, context: EmbeddingExecution
    ) -> None:
        """Прерывает Retry-After/backoff по отмене, не удерживая ресурс модели."""
        start = time.monotonic()
        until = min(deadline, context.deadline or deadline, start + max(0, seconds))
        try:
            while time.monotonic() < until:
                await self._checkpoint(deadline)
                await asyncio.sleep(min(0.2, until - time.monotonic()))
        finally:
            context.metrics["wait_ms"] += round((time.monotonic() - start) * 1000, 2)
        await self._checkpoint(deadline)

    async def _poll(self, seconds: float, deadline: float) -> None:
        """Не пересыпает оставшийся lifecycle при ожидании admission-слота."""
        context = current_embedding_execution.get()
        limit = (
            min(deadline, context.deadline)
            if context is not None and context.deadline is not None
            else deadline
        )
        await asyncio.sleep(min(seconds, max(0, limit - time.monotonic())))

    @asynccontextmanager
    async def _local_slot(self, deadline: float) -> AsyncIterator[None]:
        """FIFO ограничивает число активных запросов и ожидающих задач одного процесса."""
        slots = self._loops.setdefault(asyncio.get_running_loop(), _Slots())
        if len(slots.queue) >= self.policy.max_waiters:
            raise EmbeddingTemporaryError(
                "Очередь embedding заполнена.", reason_code="embedding_queue_full"
            )
        ticket = object()
        slots.queue.append(ticket)
        acquired = False
        try:
            while (
                slots.queue[0] is not ticket
                or slots.active >= self.policy.max_concurrency
            ):
                await self._checkpoint(deadline)
                await self._poll(0.05, deadline)
            slots.queue.popleft()
            slots.active += 1
            acquired = True
            yield
        finally:
            if acquired:
                slots.active -= 1
            elif ticket in slots.queue:
                slots.queue.remove(ticket)

    @asynccontextmanager
    async def _slot(self, deadline: float) -> AsyncIterator[None]:
        """Держит PostgreSQL session slot только на время одного физического запроса."""
        async with self._local_slot(deadline):
            while True:
                await self._checkpoint(deadline)
                if self.shared_slot is None:
                    yield
                    return
                async with self.shared_slot() as acquired:
                    if acquired:
                        yield
                        return
                await self._poll(0.1, deadline)

    def _cached(self, key: tuple) -> list[float] | None:
        """Возвращает только неизменённый точный input того же задания до истечения TTL."""
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry[0] > self.policy.cache_ttl_seconds:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return list(entry[1])

    @asynccontextmanager
    async def _timed_slot(
        self, deadline: float, context: EmbeddingExecution
    ) -> AsyncIterator[None]:
        """Учитывает ожидание также при отмене или исчерпании бюджета до получения слота."""
        start, admitted = time.monotonic(), False
        try:
            async with self._slot(deadline):
                context.metrics["wait_ms"] += round(
                    (time.monotonic() - start) * 1000, 2
                )
                admitted = True
                yield
        finally:
            if not admitted:
                context.metrics["wait_ms"] += round(
                    (time.monotonic() - start) * 1000, 2
                )

    async def execute(
        self,
        inputs: tuple[MultimodalEmbeddingInput, ...],
        *,
        identity: str,
        send: Callable[
            [tuple[MultimodalEmbeddingInput, ...], float], Awaitable[list[list[float]]]
        ],
    ) -> list[list[float]]:
        """Сохраняет телеметрию и у индексаторов/CLI, где нет HTTP-контекста задания."""
        if current_embedding_execution.get() is not None:
            return await self._execute(inputs, identity=identity, send=send)
        context = EmbeddingExecution("", self._stage, document_id=self._document_id)
        with embedding_execution(context):
            try:
                return await self._execute(inputs, identity=identity, send=send)
            finally:
                metric = context.as_dict()
                metric["batch_sizes"] = dict(Counter(map(str, metric["batch_sizes"])))
                logger.info("embedding_retrieval metrics=%s", json.dumps(metric))

    async def _execute(
        self,
        inputs: tuple[MultimodalEmbeddingInput, ...],
        *,
        identity: str,
        send: Callable[
            [tuple[MultimodalEmbeddingInput, ...], float], Awaitable[list[list[float]]]
        ],
    ) -> list[list[float]]:
        """Строит все векторы в исходном порядке без потери самостоятельных запросов."""
        context = current_embedding_execution.get() or EmbeddingExecution(
            "", "embedding"
        )
        deadline = time.monotonic() + self.policy.max_elapsed_seconds
        await self._checkpoint(deadline)
        context.metrics["logical_inputs"] += len(inputs)
        unique = {}
        order = []
        for item in inputs:
            normalized = re.sub(r"\s+", " ", item.text or "").strip()
            item = MultimodalEmbeddingInput(
                text=normalized or None,
                image_bytes=item.image_bytes,
                instruction=item.instruction,
            )
            key = hashlib.sha256(
                json.dumps(
                    [
                        identity,
                        normalized,
                        item.instruction,
                        hashlib.sha256(item.image_bytes).hexdigest()
                        if item.image_bytes
                        else None,
                    ],
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            unique.setdefault(key, item)
            order.append(key)
        context.metrics["unique_inputs"] += len(unique)
        output = {}
        pending = list(unique)
        retry_count = 0
        while pending:
            await self._checkpoint(deadline)
            cache_keys = {key: (context.job_key, key) for key in pending}
            for key in tuple(pending):
                cached = self._cached(cache_keys[key]) if context.job_key else None
                if cached is not None:
                    output[key] = cached
                    pending.remove(key)
                    context.metrics["cache_hits"] += 1
            if not pending:
                break
            capacity = (
                1 if unique[pending[0]].image_bytes else self.policy.max_batch_size
            )
            keys = pending[:capacity]
            # Изображения не объединяются с большим текстовым batch.
            if any(unique[key].image_bytes for key in keys):
                keys = keys[:1]
            try:
                async with self._timed_slot(deadline, context):
                    # Другой HTTP-запрос того же задания мог заполнить кеш во время ожидания.
                    keys = [
                        key
                        for key in keys
                        if not context.job_key or self._cached(cache_keys[key]) is None
                    ]
                    if not keys:
                        continue
                    await self._checkpoint(deadline, force_control=True)
                    batch = tuple(unique[key] for key in keys)
                    remaining = (
                        min(deadline, context.deadline or deadline) - time.monotonic()
                    )
                    context.metrics["physical_requests"] += 1
                    context.metrics["batch_sizes"].append(len(batch))
                    context.metrics["input_characters"] += sum(
                        len(item.text or "") for item in batch
                    )
                    sending = time.monotonic()
                    try:
                        vectors = await asyncio.wait_for(
                            send(batch, remaining), timeout=remaining
                        )
                    finally:
                        context.metrics["provider_ms"] += round(
                            (time.monotonic() - sending) * 1000, 2
                        )
                    for key, vector in zip(keys, vectors, strict=True):
                        output[key] = vector
                        if context.job_key:
                            self._cache[cache_keys[key]] = (
                                time.monotonic(),
                                list(vector),
                            )
                            while len(self._cache) > self.policy.cache_entries:
                                self._cache.popitem(last=False)
                        pending.remove(key)
            except (EmbeddingTemporaryError, TimeoutError) as error:
                if isinstance(error, TimeoutError):
                    error = EmbeddingTemporaryError(
                        "Таймаут embedding.", reason_code="provider_timeout"
                    )
                reasons = context.metrics["failure_reasons"]
                reasons[error.reason_code] = reasons.get(error.reason_code, 0) + 1
                if error.reason_code == "provider_timeout":
                    context.metrics["timeouts"] += 1
                if error.reason_code == "analysis_deadline_exceeded":
                    raise
                retry_count += 1
                if (
                    retry_count >= self.policy.max_attempts
                    or time.monotonic() >= deadline
                ):
                    raise EmbeddingTemporaryError(
                        "Временно недоступен embedding; бюджет повторов исчерпан "
                        f"(HTTP {error.status_code})."
                        if error.status_code is not None
                        else f"Временно недоступен embedding: {error.reason_code}; бюджет повторов исчерпан.",
                        reason_code="embedding_budget_exhausted",
                        status_code=error.status_code,
                    ) from error
                context.metrics["retries"] += 1
                backoff = min(
                    self.policy.max_backoff_seconds,
                    self.policy.backoff_seconds * 2 ** (retry_count - 1),
                )
                await self._pause(
                    max(error.retry_after or 0, random.uniform(backoff / 2, backoff)),
                    deadline,
                    context,
                )
        return [list(output[key]) for key in order]
