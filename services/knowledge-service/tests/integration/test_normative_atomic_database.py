# services/knowledge-service/tests/integration/test_normative_atomic_database.py

"""PostgreSQL: атомарная смена поколения, сбой порции, повтор и сохранность нормативного журнала."""

import os
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from uuid import uuid4

import pytest
from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    BuildNormativeRequirements,
    NormativeRequirementCatalog,
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
