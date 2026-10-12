# services/equipment-search-service/src/pdrd_equipment_search_service/application/jobs.py

"""Ограниченное выполнение EQ-поиска с восстановимой историей задания."""

import asyncio
import logging
from dataclasses import asdict, dataclass, field, replace
from math import ceil
from typing import Protocol
from uuid import UUID

from pdrd_equipment_search_service.application.job_execution import run_search_job
from pdrd_equipment_search_service.application.resolve import (
    ResolveEquipment,
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
        """Создаёт или читает то же задание с JSON-совместимыми характеристиками."""
        ...

    async def get(self, job_id: UUID) -> dict | None:
        """Читает текущее состояние."""
        ...

    async def version(self, job_id: UUID, revision: int) -> dict | None:
        """Читает неизменяемую завершённую редакцию EQ."""
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

    async def configure(self, job_id: UUID, policy: dict) -> dict:
        """Однократно закрепляет квоты логической проверки."""
        ...

    async def checkpoint(
        self, job_id: UUID, results: list[dict], checkpoint: dict
    ) -> None:
        """Сохраняет прогресс порции и версию атомарно."""
        ...

    async def queued_jobs(self) -> tuple[UUID, ...]:
        """Читает ограниченную долговечную очередь с приоритетом коротких заданий."""
        ...

    async def release(self, job_id: UUID) -> bool:
        """Возвращает фоновое задание в очередь после сохранённой порции."""
        ...

    async def resume(self, job_id: UUID, revision: int, max_runs: int) -> bool:
        """Продолжает задание по актуальной версии и ограниченному числу запусков."""
        ...


@dataclass(slots=True)
class SearchJobService:
    """Дедуплицирует модели и ограничивает работу всего задания."""

    repository: JobRepository
    resolver: ResolveEquipment
    max_models: int = 500
    max_seconds: float = 180.0
    max_concurrent: int = 2
    max_vision_calls: int = 2
    max_queries: int = 6
    max_downloads: int = 3
    queries_per_model: int = 3
    downloads_per_model: int = 2
    inline_models: int = 20
    batch_size: int = 10
    adaptive_vision_ceiling: int = 8
    max_resume_runs: int = 3
    _watcher: asyncio.Task[None] | None = field(default=None, init=False)
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
                by_key[identity.key] = replace(
                    previous,
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
        payload = [
            {**asdict(identity), "properties": list(identity.properties)}
            for identity in unique
        ]
        state = await self.repository.create_or_get(job_id, payload, allow_unverified)
        state = await self.repository.configure(job_id, self.policy(len(unique)))
        if state["status"] == "queued":
            self._schedule(job_id)
        return state

    async def cancel(self, job_id: UUID) -> bool:
        """Фиксирует отмену и прерывает активный запрос до следующего шага."""
        state = await self.repository.get(job_id)
        if state is None:
            return False
        accepted = await self.repository.cancel(job_id)
        if not accepted:
            return False
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        state = await self.repository.get(job_id)
        if state["status"] in {"queued", "running"}:
            await self.repository.finish(
                job_id,
                "cancelled",
                state.get("results", []),
                "Поиск отменён пользователем.",
            )
            await self.repository.append_event(
                job_id, "cancelled", "Поиск отменён пользователем."
            )
        return True

    async def _run(self, job_id: UUID) -> None:
        """Выполняет справедливые порции через отдельный сценарий с checkpoint."""
        await run_search_job(self, job_id)

    def policy(self, models: int) -> dict:
        """Ограничивает квоты числом моделей без выдачи каждому собственного GPU-бюджета."""
        return {
            "background": models > self.inline_models,
            "models": models,
            "query_limit": min(
                1500, max(self.max_queries, models * self.queries_per_model)
            ),
            "download_limit": min(
                1000, max(self.max_downloads, models * self.downloads_per_model)
            ),
            "vision_limit": min(
                self.adaptive_vision_ceiling,
                max(self.max_vision_calls, ceil(models / 50)),
            )
            if self.max_vision_calls
            else 0,
            "queries_per_model": self.queries_per_model,
            "downloads_per_model": self.downloads_per_model,
            "max_resume_runs": self.max_resume_runs,
        }

    async def resume(self, job_id: UUID, revision: int) -> dict:
        """Проверяет версию и запускает продолжение сохранённого задания."""
        active = self._tasks.get(job_id)
        if active is not None and not active.done():
            raise ValueError(
                "Предыдущая порция ещё завершает запись. Повторите продолжение."
            )
        saved = await self.repository.get(job_id)
        limit = min(
            self.max_resume_runs,
            int(
                (saved or {})
                .get("policy", {})
                .get("max_resume_runs", self.max_resume_runs)
            ),
        )
        if not await self.repository.resume(job_id, revision, limit):
            raise ValueError(
                "Версия изменилась, задание завершено или лимит продолжений исчерпан."
            )
        state = await self.repository.get(job_id)
        self._schedule(job_id)
        return state

    def _schedule(self, job_id: UUID) -> None:
        """Старый callback завершения не удаляет новое продолжение из реестра."""
        previous = self._tasks.get(job_id)
        if previous is not None and not previous.done():
            return
        if previous is None and len(self._tasks) >= self.max_concurrent * 2:
            return
        task = asyncio.create_task(self._run(job_id))
        self._tasks[job_id] = task

        def finished(current: asyncio.Task) -> None:
            """Удаляет только ту задачу, для которой зарегистрировано завершение."""
            if self._tasks.get(job_id) is current:
                self._tasks.pop(job_id, None)

        task.add_done_callback(finished)

    async def start_queue(self) -> None:
        """Подбирает долговечные задания после перезапуска и поддерживает очередь."""

        async def watch() -> None:
            """Проверяет только собственную очередь с ограниченной частотой."""
            while True:
                try:
                    queued = await self.repository.queued_jobs()
                except Exception:
                    logging.getLogger(__name__).warning(
                        "event=equipment_queue_unavailable"
                    )
                    await asyncio.sleep(1)
                    continue
                for job_id in queued:
                    self._schedule(job_id)
                await asyncio.sleep(1)

        if self._watcher is None:
            self._watcher = asyncio.create_task(watch())

    async def shutdown(self) -> None:
        """Останавливает локальные задачи при завершении процесса."""
        tasks = list(self._tasks.values())
        if self._watcher is not None:
            tasks.append(self._watcher)
            self._watcher = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
