# services/knowledge-service/tests/integration/test_normative_atomic_database.py

"""PostgreSQL: атомарная смена поколения, сбой порции, повтор и сохранность нормативного журнала."""

import asyncio
import os
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from uuid import UUID, uuid4

import pytest
from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    BuildNormativeRequirements,
    NormativeRequirementCatalog,
    NormativeRequirementsConflictError,
)
from pdrd_knowledge_service.application.use_cases.recover_normative_requirements import (
    RecoverNormativeRequirements,
)
from pdrd_knowledge_service.core.settings import Settings
from pdrd_knowledge_service.domain.normative_catalog import NormativeSection
from pdrd_knowledge_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_knowledge_service.infrastructure.database.normative_requirements import (
    SqlAlchemyNormativeRequirementRepository,
)
from pdrd_knowledge_service.infrastructure.database.section_locks import (
    PostgresCatalogSectionLocks,
)
from pdrd_knowledge_service.infrastructure.database.unit_of_work import (
    SqlAlchemyNormativeCatalogUnitOfWork,
)

from ..atomic_normative_support import (
    ACTOR_ID,
    Extractor,
    Storage,
    document,
    proof_payload,
)

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        os.getenv("PDRD_RUN_DATABASE_TESTS") != "1",
        reason="Нужен изолированный PostgreSQL Knowledge runner.",
    ),
]


async def test_real_generation_swap_failure_retry_status_and_cascade(monkeypatch):
    """Прежние требования видимы до полного нового commit; повтор не дублирует строки."""
    settings = Settings(_env_file=None)
    if (
        settings.database.host != "knowledge-test-postgres"
        or settings.database.name != "pdrd_knowledge_test"
    ):
        pytest.skip("Только изолированная БД ops/compose.knowledge-test.yaml.")
    engine = build_async_engine(settings.database)
    factory = partial(
        SqlAlchemyNormativeCatalogUnitOfWork, build_session_factory(engine)
    )
    section_id, document_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    source = replace(
        document(),
        document_id=document_id,
        section_id=section_id,
        storage_key=f"test/{document_id}.pdf",
    )
    catalog = NormativeRequirementCatalog(factory)
    worker = BuildNormativeRequirements(factory, Storage(), Extractor(), batch_size=1)
    try:
        async with factory() as uow:
            await uow.sections.add(
                NormativeSection(
                    section_id, "Атомарный тест", "Стендовый промпт", now, now
                )
            )
            await uow.documents.add(source)
            await uow.commit()
        queued = await catalog.queue(document_id=document_id)
        old = await worker.execute(
            document_id=document_id, generation_id=queued["generation_id"]
        )
        old_rows = (await catalog.list(document_id=document_id))["requirements"]
        proof = await catalog.record_status(
            document_id=document_id, payload=proof_payload(), actor_id=ACTOR_ID
        )
        queued = await catalog.queue(document_id=document_id)
        original_write, calls = SqlAlchemyNormativeRequirementRepository.write_batch, []

        async def fail_second(self, *args, **kwargs):
            """Вторая порция прерывается после реального commit первой."""
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("batch failure")
            return await original_write(self, *args, **kwargs)

        monkeypatch.setattr(
            SqlAlchemyNormativeRequirementRepository, "write_batch", fail_second
        )
        with pytest.raises(RuntimeError, match="batch failure"):
            await worker.execute(
                document_id=document_id, generation_id=queued["generation_id"]
            )
        preserved = await catalog.list(document_id=document_id)
        assert preserved["generation_id"] == old["generation_id"]
        assert [row["requirement_id"] for row in preserved["requirements"]] == [
            row["requirement_id"] for row in old_rows
        ]
        async with factory() as uow:
            assert (
                await uow.requirements.count_requirements(queued["generation_id"]) == 1
            )
            assert (await uow.requirements.get_generation(queued["generation_id"]))[
                "state"
            ] == "failed"
        monkeypatch.setattr(
            SqlAlchemyNormativeRequirementRepository, "write_batch", original_write
        )
        await catalog.queue(document_id=document_id)
        new = await worker.execute(
            document_id=document_id, generation_id=queued["generation_id"]
        )
        assert new["count"] == old["count"]
        assert (await catalog.list(document_id=document_id))["generation_id"] == new[
            "generation_id"
        ]
        assert (await catalog.evidence(document_id=document_id))["status_evidence"] == [
            proof
        ]
        async with factory() as uow:
            assert (await uow.requirements.get_generation(old["generation_id"]))[
                "active"
            ] is False
            assert (
                await uow.requirements.count_requirements(old["generation_id"])
                == old["count"]
            )
            await uow.documents.delete(document_id)
            await uow.commit()
        async with factory() as uow:
            assert await uow.requirements.get_generation(new["generation_id"]) is None
            assert await uow.requirements.count_requirements(new["generation_id"]) == 0
            assert (
                await uow.requirements.status_evidence(str(document_id), source.sha256)
                == ()
            )
    finally:
        async with factory() as uow:
            await uow.sections.delete(section_id)
            await uow.commit()
        await engine.dispose()


@pytest.fixture
async def atomic_database():
    """Создаёт раздел только в штатной изолированной PostgreSQL Knowledge DB."""
    settings = Settings(_env_file=None)
    if (
        settings.database.host != "knowledge-test-postgres"
        or settings.database.name != "pdrd_knowledge_test"
    ):
        pytest.skip("Только изолированная БД ops/compose.knowledge-test.yaml.")
    engine = build_async_engine(settings.database)
    factory = partial(
        SqlAlchemyNormativeCatalogUnitOfWork, build_session_factory(engine)
    )
    section_id = uuid4()
    now = datetime.now(UTC)
    async with factory() as uow:
        await uow.sections.add(
            NormativeSection(
                section_id, "Atomic admission test", "Стендовый промпт", now, now
            )
        )
        await uow.commit()
    try:
        yield engine, factory, section_id
    finally:
        async with factory() as uow:
            await uow.sections.delete(section_id)
            await uow.commit()
        await engine.dispose()


async def test_real_parallel_admission_and_recovery_share_one_global_limit(
    atomic_database,
):
    """Отдельные SQL-транзакции не могут одновременно допустить три jobs при limit=2."""
    engine, factory, section_id = atomic_database
    identifiers = [uuid4() for _ in range(3)]
    async with factory() as uow:
        for identifier in identifiers:
            await uow.documents.add(
                replace(
                    document(),
                    document_id=identifier,
                    section_id=section_id,
                    storage_key=f"test/{identifier}.pdf",
                )
            )
        await uow.commit()
    catalog = NormativeRequirementCatalog(factory)
    generations = await asyncio.gather(
        *(catalog.queue(document_id=identifier) for identifier in identifiers)
    )
    assert sum(not row["dispatch_pending"] for row in generations) == 2
    async with factory() as uow:
        assert await uow.requirements.in_flight() == 2
        assert len(await uow.outbox.get_pending(limit=10)) == 2
    admitted = next(row for row in generations if not row["dispatch_pending"])
    await BuildNormativeRequirements(
        factory,
        Storage(),
        Extractor(),
        section_locks=PostgresCatalogSectionLocks(engine),
    ).execute(
        document_id=UUID(admitted["document_id"]),
        generation_id=admitted["generation_id"],
    )
    report = await RecoverNormativeRequirements(catalog).execute(
        section_id=section_id, only_pending=True
    )
    assert report["selected"] == 1
    async with factory() as uow:
        assert await uow.requirements.in_flight() == 2
        assert len(await uow.outbox.get_pending(limit=10)) == 3


async def test_real_document_worker_lock_and_status_fencing_prevent_obsolete_publish(
    atomic_database,
):
    """Новый snapshot не извлекается параллельно старому; прежняя попытка теряет право публикации."""
    engine, factory, section_id = atomic_database
    document_id = uuid4()
    async with factory() as uow:
        await uow.documents.add(
            replace(
                document(),
                document_id=document_id,
                section_id=section_id,
                storage_key=f"test/{document_id}.pdf",
            )
        )
        await uow.commit()
    catalog = NormativeRequirementCatalog(factory)
    started, release, next_started = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class PausedExtractor(Extractor):
        """Первая попытка держит advisory lock во время внешнего PDF-адаптера."""

        async def extract_pages(self, **kwargs):
            """Останавливает первую попытку без открытой SQL-транзакции."""
            started.set()
            await release.wait()
            return await super().extract_pages(**kwargs)

    class NextExtractor(Extractor):
        """Фиксирует фактическое начало извлечения новой попытки."""

        async def extract_pages(self, **kwargs):
            """Вызывается только после получения блокировки документа."""
            next_started.set()
            return await super().extract_pages(**kwargs)

    queued = await catalog.queue(document_id=document_id)
    old_task = asyncio.create_task(
        BuildNormativeRequirements(
            factory,
            Storage(),
            PausedExtractor(),
            section_locks=PostgresCatalogSectionLocks(engine),
        ).execute(document_id=document_id, generation_id=queued["generation_id"])
    )
    new_task = None
    try:
        await asyncio.wait_for(started.wait(), 3)
        await catalog.record_status(
            document_id=document_id, payload=proof_payload(), actor_id=ACTOR_ID
        )
        fresh = await catalog.queue(document_id=document_id)
        new_task = asyncio.create_task(
            BuildNormativeRequirements(
                factory,
                Storage(),
                NextExtractor(),
                section_locks=PostgresCatalogSectionLocks(engine),
            ).execute(document_id=document_id, generation_id=fresh["generation_id"])
        )
        await asyncio.sleep(0.1)
        assert not next_started.is_set()
        release.set()
        with pytest.raises(NormativeRequirementsConflictError):
            await old_task
        result = await asyncio.wait_for(new_task, 5)
        assert result["state"] == "ready" and next_started.is_set()
        async with factory() as uow:
            assert (await uow.requirements.active_generation(str(document_id)))[
                "generation_id"
            ] == fresh["generation_id"]
            assert (await uow.requirements.get_generation(queued["generation_id"]))[
                "error_code"
            ] == "superseded_source_or_status"
    finally:
        release.set()
        await asyncio.gather(
            *(task for task in (old_task, new_task) if task is not None),
            return_exceptions=True,
        )
