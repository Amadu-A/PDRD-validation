# services/equipment-search-service/src/pdrd_equipment_search_service/application/jobs.py

"""Ограниченное выполнение EQ-поиска с восстановимой историей задания."""

import asyncio
from dataclasses import asdict, dataclass, field
from time import perf_counter
from typing import Protocol
from uuid import UUID

from pdrd_equipment_search_service.application.resolve import (
    ResolveEquipment,
    SearchCancelled,
)
from pdrd_equipment_search_service.application.search_budget import (
    EquipmentSearchBudget,
)
from pdrd_equipment_search_service.application.vision_budget import (
    EquipmentVisionBudget,
)
from pdrd_equipment_search_service.core.observability import log_execution_time
from pdrd_equipment_search_service.domain.equipment import EquipmentIdentity


class JobRepository(Protocol):
    """Порт долговечного журнала поиска."""

    async def is_ready(self) -> bool:
        """Проверяет готовность долговечного хранилища."""
        ...

    async def create_or_get(
        self,
        job_id: UUID,
        identities: list[dict],
        allow_unverified: bool,
    ) -> dict:
        """Создаёт или читает то же задание."""
        ...

    async def get(self, job_id: UUID) -> dict | None:
        """Читает текущее состояние."""
        ...

    async def claim(self, job_id: UUID) -> bool:
        """Захватывает выполнение."""
        ...

    async def cancel(self, job_id: UUID) -> bool:
        """Запрашивает отмену."""
        ...

    async def is_cancelled(self, job_id: UUID) -> bool:
        """Проверяет отмену."""
        ...

    async def append_event(
        self,
        job_id: UUID,
        event_type: str,
        message: str,
    ) -> int:
        """Пишет следующее событие."""
        ...

    async def events_after(self, job_id: UUID, sequence: int) -> list[dict]:
        """Читает события после номера."""
        ...

    async def finish(
        self,
        job_id: UUID,
        status: str,
        results: list[dict],
        warning: str = "",
    ) -> None:
        """Фиксирует итог задания."""
        ...


@dataclass(slots=True)
class SearchJobService:
    """Дедуплицирует модели и ограничивает работу всего задания."""

    repository: JobRepository
    resolver: ResolveEquipment
    max_models: int = 12
    max_seconds: float = 180.0
    max_concurrent: int = 2
    max_vision_calls: int = 2
    max_queries: int = 6
    max_downloads: int = 3
    _tasks: dict[UUID, asyncio.Task[None]] = field(default_factory=dict, init=False)
    _semaphore: asyncio.Semaphore = field(init=False)
    _active: set[UUID] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        """Создаёт общий для сервиса лимит одновременных поисков."""
        self._semaphore = asyncio.Semaphore(self.max_concurrent)

    @log_execution_time(operation="equipment.start")
    async def start(
        self,
        job_id: UUID,
        identities: list[EquipmentIdentity],
        allow_unverified: bool,
    ) -> dict:
        """Повторяет запрос идемпотентно и запускает очередь один раз."""
        by_key: dict[tuple[str, str, str], EquipmentIdentity] = {}
        for identity in identities:
            previous = by_key.get(identity.key)
            if previous is None:
                by_key[identity.key] = identity
            else:
                merged = tuple(
                    dict.fromkeys((*previous.properties, *identity.properties))
                )[:8]
                by_key[identity.key] = EquipmentIdentity(
                    manufacturer=previous.manufacturer,
                    model=previous.model,
                    variant=previous.variant,
                    status=(
                        "resolved"
                        if previous.status == identity.status == "resolved"
                        else "needs_review"
                    ),
                    confidence=min(previous.confidence, identity.confidence),
                    properties=merged,
                )
        unique = list(by_key.values())
        if len(unique) > self.max_models:
            raise ValueError("Превышен лимит уникальных моделей в задании.")
        payload = [asdict(identity) for identity in unique]
        state = await self.repository.create_or_get(job_id, payload, allow_unverified)
        if state["status"] == "queued" and job_id not in self._tasks:
            task = asyncio.create_task(self._run(job_id))
            self._tasks[job_id] = task
            task.add_done_callback(lambda _task: self._tasks.pop(job_id, None))
        return state

    async def cancel(self, job_id: UUID) -> bool:
        """Фиксирует отмену и прерывает активный запрос до следующего шага."""
        state = await self.repository.get(job_id)
        if state is None:
            return False
        accepted = await self.repository.cancel(job_id)
        if not accepted:
            return False
        if state["status"] == "queued":
            await self.repository.finish(
                job_id, "cancelled", [], "Поиск отменён до запуска."
            )
            await self.repository.append_event(
                job_id, "cancelled", "Поиск отменён до запуска."
            )
        task = self._tasks.get(job_id)
        if task is not None and job_id in self._active:
            task.cancel()
        return True

    async def _run(self, job_id: UUID) -> None:
        """Записывает частичный результат при отказе EQ без срыва основного анализа."""
        async with self._semaphore:
            if not await self.repository.claim(job_id):
                return
            state = await self.repository.get(job_id)
            if state is None:
                return
            await self.repository.append_event(
                job_id, "started", "Поиск документации начат."
            )
            results: list[dict] = []
            vision_budget = EquipmentVisionBudget(self.max_vision_calls)
            search_budget = EquipmentSearchBudget(self.max_queries, self.max_downloads)
            status = "completed"
            warning = ""
            try:
                self._active.add(job_id)
                async with asyncio.timeout(self.max_seconds):
                    for data in state["identities"]:
                        if await self.repository.is_cancelled(job_id):
                            raise SearchCancelled
                        identity = EquipmentIdentity(**data)
                        await self.repository.append_event(
                            job_id,
                            "model_started",
                            f"Поиск документации: {identity.manufacturer} {identity.model}"[
                                :200
                            ],
                        )
                        started = perf_counter()
                        remaining_before = vision_budget.remaining
                        metric_offset = len(vision_budget.metrics)
                        queries_before = search_budget.queries_remaining
                        downloads_before = search_budget.downloads_remaining
                        result = await self.resolver.execute(
                            identity,
                            allow_unverified=state["allow_unverified"],
                            should_stop=lambda: self.repository.is_cancelled(job_id),
                            vision_budget=vision_budget,
                            search_budget=search_budget,
                        )
                        model_metrics = vision_budget.metrics[metric_offset:]
                        cached_calls = sum(
                            metric.get("done_reason") == "cache_hit"
                            for metric in model_metrics
                        )
                        results.append(
                            {
                                **asdict(result),
                                "metrics": {
                                    "duration_ms": round(
                                        (perf_counter() - started) * 1000, 3
                                    ),
                                    "vision_calls": remaining_before
                                    - vision_budget.remaining
                                    - cached_calls,
                                    "vision_cache_hits": cached_calls,
                                    "prompt_tokens": sum(
                                        int(metric.get("prompt_eval_count") or 0)
                                        for metric in model_metrics
                                    ),
                                    "output_tokens": sum(
                                        int(metric.get("eval_count") or 0)
                                        for metric in model_metrics
                                    ),
                                    "queries": queries_before
                                    - search_budget.queries_remaining,
                                    "downloads": downloads_before
                                    - search_budget.downloads_remaining,
                                    "vision_metrics": model_metrics,
                                },
                            }
                        )
                        await self.repository.append_event(
                            job_id,
                            "model_finished",
                            f"Модель {identity.model}: {result.status}"[:200],
                        )
                        if result.status == "incomplete":
                            status = "incomplete"
            except (SearchCancelled, asyncio.CancelledError):
                status = "cancelled"
                warning = "Поиск отменён пользователем."
            except TimeoutError:
                status = "incomplete"
                warning = "Превышен бюджет времени поиска."
            except Exception:
                status = "incomplete"
                warning = "Оборудование проверено не полностью из-за ошибки сервиса."
            finally:
                self._active.discard(job_id)
            await self.repository.finish(job_id, status, results, warning)
            await self.repository.append_event(
                job_id, status, warning or "Поиск завершён."
            )

    async def shutdown(self) -> None:
        """Останавливает локальные задачи при завершении процесса."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
