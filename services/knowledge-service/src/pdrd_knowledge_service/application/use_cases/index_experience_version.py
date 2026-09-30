# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/index_experience_version.py

"""Обработка одной вручную выбранной версии без автоматического обхода всего каталога."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, replace
from uuid import UUID

from pdrd_knowledge_service.application.ports.experience_feed import (
    ExperienceFeed,
    ExperienceFeedError,
)
from pdrd_knowledge_service.application.ports.experience_versions import (
    ExperienceVersionQueue,
)
from pdrd_knowledge_service.application.use_cases.index_experience import (
    SyncExperienceIndex,
)
from pdrd_knowledge_service.core.observability import log_execution_time
from pdrd_knowledge_service.domain.experience_index import (
    ExampleReference,
    TrustedExample,
)


@dataclass(frozen=True, slots=True)
class SelectedExperienceFeed:
    """Фиксированный состав версии проверяется живым владельцем перед каждой записью."""

    source: ExperienceFeed
    members: tuple[TrustedExample, ...]

    async def page(
        self, *, after: UUID | None, limit: int
    ) -> tuple[tuple[TrustedExample, ...], UUID | None]:
        """Члены, отозванные после запуска, прерывают сборку вместо неполной публикации."""
        page = tuple(
            sorted(
                (
                    item
                    for item in self.members
                    if after is None or item.reference.example_id > after
                ),
                key=lambda item: item.reference.example_id,
            )[:limit]
        )
        if page:
            current = await self.source.verify(tuple(item.reference for item in page))
            if {item.reference for item in current} != {
                item.reference for item in page
            }:
                raise ExperienceFeedError("Состав версии изменён или область отозвана.")
        next_after = page[-1].reference.example_id if len(page) == limit else None
        return page, next_after

    async def verify(
        self, references: tuple[ExampleReference, ...]
    ) -> tuple[TrustedExample, ...]:
        """Доверие не переносится из старого manifest на исправленный текст."""
        allowed = {item.reference for item in self.members}
        return await self.source.verify(
            tuple(item for item in references if item in allowed)
        )

    async def crop(self, *, example: TrustedExample, index: int) -> bytes:
        """Crop остаётся у владельца и проверяется по своему fingerprint."""
        if example.reference not in {item.reference for item in self.members}:
            raise ExperienceFeedError("Пример не входит в выбранную версию.")
        return await self.source.crop(example=example, index=index)


@dataclass(frozen=True, slots=True)
class RunExperienceVersion:
    """Аренда с heartbeat; ready означает готовность индекса, а не допуск в рабочий E."""

    queue: ExperienceVersionQueue
    index: SyncExperienceIndex
    worker: str
    model: str

    async def execute(self) -> dict:
        """Пустая очередь не вызывает embedding и не создаёт коллекцию."""
        job = await self.queue.claim(
            worker=self.worker,
            model=self.model,
            identity=self.index.identity,
            dimension=self.index.dimension,
        )
        if job is None:
            return {"claimed": False}

        async def heartbeat() -> None:
            """Продлевает аренду длительной GPU задачи; потеря аренды отменяет сборку."""
            while True:
                await asyncio.sleep(45)
                await self.queue.finish(
                    job_id=job.id, worker=self.worker, status="heartbeat"
                )

        @log_execution_time(operation="experience_version_build")
        async def build() -> dict:
            """Сборка всех выбранных примеров должна закончиться полностью."""
            feed = SelectedExperienceFeed(self.index.source, job.members)
            index = replace(self.index, source=feed, collection=job.collection)
            result = await index.execute()
            if result["stale"]:
                raise ExperienceFeedError(
                    "Во время индексации изменились выбранные примеры."
                )
            for offset in range(0, len(job.members), 100):
                references = tuple(
                    item.reference for item in job.members[offset : offset + 100]
                )
                if {item.reference for item in await feed.verify(references)} != set(
                    references
                ):
                    raise ExperienceFeedError(
                        "Состав версии изменён перед публикацией."
                    )
            await self.queue.finish(job_id=job.id, worker=self.worker, status="ready")
            return {"claimed": True, "version_id": str(job.id), **result}

        pulse, task = asyncio.create_task(heartbeat()), asyncio.create_task(build())
        try:
            done, _ = await asyncio.wait(
                (pulse, task), return_when=asyncio.FIRST_COMPLETED
            )
            if pulse in done:
                await pulse
            return await task
        except Exception:
            with suppress(Exception):
                await self.queue.finish(
                    job_id=job.id,
                    worker=self.worker,
                    status="failed",
                    error="Индексация не завершена. Проверьте модели и актуальность примеров; "
                    "создайте новую версию после исправления.",
                )
            raise
        finally:
            pulse.cancel()
            task.cancel()
            await asyncio.gather(pulse, task, return_exceptions=True)
