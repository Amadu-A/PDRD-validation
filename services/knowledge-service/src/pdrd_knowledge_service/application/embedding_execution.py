# services/knowledge-service/src/pdrd_knowledge_service/application/embedding_execution.py

"""Контекст поиска: задание, deadline, отмена и компактные счётчики без содержимого документа.

HTTP устанавливает контекст, адаптеры обновляют общий объект метрик. Сами правила не знают
о FastAPI, SQL, векторах и транспортном способе проверки lifecycle.
"""

import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import uuid4

from pdrd_knowledge_service.application.ports.embedding import EmbeddingTemporaryError


class EmbeddingCancelledError(RuntimeError):
    """Ожидаемая durable отмена до нового физического запроса."""


@dataclass(slots=True)
class EmbeddingExecution:
    """Изолирует переиспользование по заданию; deadline уточняется доверенным Gateway."""

    job_key: str
    stage: str
    check_control: Callable[[], Awaitable[dict]] | None = None
    deadline: float | None = None
    metrics: dict = field(
        default_factory=lambda: {
            "logical_inputs": 0,
            "unique_inputs": 0,
            "physical_requests": 0,
            "batch_sizes": [],
            "retries": 0,
            "wait_ms": 0.0,
            "provider_ms": 0.0,
            "cache_hits": 0,
            "http_429": 0,
            "http_503": 0,
            "timeouts": 0,
            "input_characters": 0,
            "prompt_tokens": 0,
            "failure_reasons": {},
        }
    )
    _last_control: float = -1e9
    operation_id: str = field(default_factory=lambda: str(uuid4()))
    document_id: str = ""

    async def checkpoint(self, *, force_control: bool = False) -> None:
        """Проверяет отмену перед inference; в ожидании опрашивает владельца не чаще раза в секунду."""
        now = time.monotonic()
        if self.check_control is not None and (
            force_control
            or now - self._last_control >= 1
            or (self.deadline is not None and now >= self.deadline)
        ):
            control = await self.check_control()
            self._last_control = time.monotonic()
            self.job_key = str(control.get("job_id") or self.job_key)
            if control.get("cancelled"):
                raise EmbeddingCancelledError(
                    "Анализ отменён во время ожидания embedding."
                )
            limit = time.monotonic() + max(0, float(control["remaining_seconds"]))
            self.deadline = (
                min(self.deadline, limit) if self.deadline is not None else limit
            )
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise EmbeddingTemporaryError(
                "Истёк lifecycle deadline задания.",
                reason_code="analysis_deadline_exceeded",
            )

    def as_dict(self) -> dict:
        """Возвращает только компактные счётчики с корреляцией задания и стадии."""
        return {
            "job_id": self.job_key or None,
            "document_id": self.document_id or None,
            "operation_id": self.operation_id,
            "stage": self.stage,
            **self.metrics,
            "batch_sizes": list(self.metrics["batch_sizes"]),
            "failure_reasons": dict(self.metrics["failure_reasons"]),
        }


current_embedding_execution: ContextVar[EmbeddingExecution | None] = ContextVar(
    "embedding_execution", default=None
)


@contextmanager
def embedding_execution(execution: EmbeddingExecution) -> Iterator[None]:
    """Передаёт один контекст всем дочерним задачам поиска и восстанавливает предыдущий."""
    token = current_embedding_execution.set(execution)
    try:
        yield
    finally:
        current_embedding_execution.reset(token)
