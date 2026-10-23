# services/knowledge-service/tests/integration/test_normative_batch_database.py

"""Реальный разрыв INSERT и большие JSONB только в изолированной Knowledge PostgreSQL."""

import asyncio
import os
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from pdrd_knowledge_service.application.normative_generation_integrity import (
    NormativeGenerationIntegrityError,
    validate_generation,
)
from pdrd_knowledge_service.application.use_cases import (
    normative_requirements as use_cases,
)
from pdrd_knowledge_service.application.use_cases.normative_requirements import (
    BuildNormativeRequirements,
    NormativeRequirementCatalog,
)
from pdrd_knowledge_service.infrastructure.database.models import (
    NormativeRequirementModel,
)
from pdrd_knowledge_service.infrastructure.database.normative_requirements import (
    SqlAlchemyNormativeRequirementRepository,
)
from sqlalchemy import select, text

from ..atomic_normative_support import Extractor, Storage, document
from ..unit.test_normative_batch_resilience import atomic_rows, many_pages
from .test_normative_atomic_database import atomic_database as atomic_database

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        os.getenv("PDRD_RUN_DATABASE_TESTS") != "1",
        reason="Нужен изолированный PostgreSQL Knowledge runner.",
    ),
]


async def add_document(factory, section_id):
    """Добавляет только фиктивный норматив в тестовый раздел существующей fixture."""
    identifier = uuid4()
    async with factory() as uow:
        await uow.documents.add(
            replace(document(), document_id=identifier, section_id=section_id)
        )
        await uow.commit()
    return identifier


@pytest.mark.parametrize("batch_size", [1, 10, 20, 100])
async def test_real_large_generation_batch_sizes_and_idempotent_insert(
    atomic_database, batch_size
):
    """Все порции дают одинаковый digest; повтор последней записи не создаёт дубликат."""
    _, factory, section_id = atomic_database
    identifier = await add_document(factory, section_id)
    queued = await NormativeRequirementCatalog(factory).queue(document_id=identifier)
    ready = await BuildNormativeRequirements(
        factory, Storage(), Extractor(many_pages()), batch_size=batch_size
    ).execute(
        document_id=identifier,
        generation_id=queued["generation_id"],
    )
    async with factory() as uow:
        result = await validate_generation(uow.requirements, ready)
        assert result["actual_count"] == ready["count"] > 200
        rows = await uow.requirements.list_requirements(ready["generation_id"], 0, 200)
        assert len({row["requirement_id"] for row in rows}) == 200
        ordinals = list(
            await uow._session.scalars(
                select(NormativeRequirementModel.ordinal)
                .where(
                    NormativeRequirementModel.generation_id
                    == UUID(ready["generation_id"]),
                )
                .order_by(NormativeRequirementModel.ordinal)
            )
        )
        assert ordinals == list(range(ready["count"]))
    # Идемпотентность репозитория проверяется в отдельном неопубликованном поколении.
    async with factory() as uow:
        duplicate = {
            **ready,
            "generation_id": str(uuid4()),
            "state": "building",
            "active": False,
        }
        await uow.requirements.save_generation(duplicate)
        atoms = atomic_rows(identifier, pages=many_pages())[:batch_size]
        await uow.requirements.write_batch(duplicate["generation_id"], 0, atoms)
        await uow.requirements.write_batch(duplicate["generation_id"], 0, atoms)
        assert await uow.requirements.count_requirements(
            duplicate["generation_id"]
        ) == len(atoms)
        await uow.commit()


async def test_real_disconnect_during_insert_retries_only_second_batch(
    atomic_database, monkeypatch
):
    """pg_terminate_backend во время PgSleep внутри INSERT откатывает порцию, не первую транзакцию."""
    engine, factory, section_id = atomic_database
    identifier = await add_document(factory, section_id)
    queued = await NormativeRequirementCatalog(factory).queue(document_id=identifier)
    generation_id = queued["generation_id"]
    suffix = uuid4().hex
    function, trigger = f"atomic_pause_{suffix}", f"atomic_pause_{suffix}"
    async with engine.begin() as connection:
        await connection.execute(
            text(f"""CREATE FUNCTION knowledge.{function}() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN PERFORM pg_sleep(0.5); RETURN NEW; END; $$""")
        )
        await connection.execute(
            text(f"""CREATE TRIGGER {trigger} BEFORE INSERT ON knowledge.normative_requirements
            FOR EACH ROW WHEN (NEW.generation_id = '{generation_id}'::uuid AND NEW.ordinal = 20)
            EXECUTE FUNCTION knowledge.{function}()""")
        )
    write = SqlAlchemyNormativeRequirementRepository.write_batch
    calls, killed = [], []

    async def terminate_when_in_insert(pid):
        """Завершает ровно backend тестовой UOW после входа в серверный trigger."""
        async with engine.connect() as killer:
            for _ in range(100):
                state = (
                    await killer.execute(
                        text(
                            "SELECT wait_event,query FROM pg_stat_activity WHERE pid=:pid"
                        ),
                        {"pid": pid},
                    )
                ).first()
                if (
                    state
                    and state.wait_event == "PgSleep"
                    and "INSERT INTO knowledge.normative_requirements" in state.query
                ):
                    assert await killer.scalar(
                        text("SELECT pg_terminate_backend(:pid)"), {"pid": pid}
                    )
                    killed.append(pid)
                    return
                await killer.rollback()
                await asyncio.sleep(0.01)
        pytest.fail("INSERT не вошёл в тестовую задержку; отключение не имитировано")

    async def tracked(self, current_generation, offset, requirements):
        """Сохраняет реальные PID и выполняет настоящий multi-row INSERT."""
        pid = await self.session.scalar(text("SELECT pg_backend_pid()"))
        calls.append((offset, pid))
        if offset == 20 and not killed:
            kill = asyncio.create_task(terminate_when_in_insert(pid))
            try:
                return await write(self, current_generation, offset, requirements)
            finally:
                await kill
        return await write(self, current_generation, offset, requirements)

    monkeypatch.setattr(
        SqlAlchemyNormativeRequirementRepository, "write_batch", tracked
    )
    try:
        ready = await BuildNormativeRequirements(
            factory, Storage(), Extractor(many_pages()), batch_size=20
        ).execute(
            document_id=identifier,
            generation_id=generation_id,
        )
        assert ready["state"] == "ready" and ready["attempt_count"] == 1
        assert len(killed) == 1 and [offset for offset, _ in calls].count(0) == 1
        second = [pid for offset, pid in calls if offset == 20]
        assert len(second) == 2 and second[0] != second[1]
        async with factory() as uow:
            assert (await validate_generation(uow.requirements, ready))[
                "actual_count"
            ] == ready["count"]
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    f"DROP TRIGGER IF EXISTS {trigger} ON knowledge.normative_requirements"
                )
            )
            await connection.execute(
                text(f"DROP FUNCTION IF EXISTS knowledge.{function}()")
            )


async def test_real_jsonb_size_bounds_and_incomplete_ordinals_cannot_publish(
    atomic_database, monkeypatch
):
    """Крупные параметры сохраняются целиком; дырка ordinal блокирует готовность."""
    _, factory, section_id = atomic_database
    identifier = await add_document(factory, section_id)
    atoms = tuple(
        replace(item, context=("Ж" * 100_000,))
        for item in atomic_rows(identifier, pages=many_pages())[:20]
    )
    monkeypatch.setattr(
        use_cases, "extract_normative_requirements", lambda **kwargs: iter(atoms)
    )
    catalog = NormativeRequirementCatalog(factory)
    queued = await catalog.queue(document_id=identifier)
    ready = await BuildNormativeRequirements(
        factory, Storage(), Extractor(many_pages()), batch_size=20
    ).execute(
        document_id=identifier,
        generation_id=queued["generation_id"],
    )
    async with factory() as uow:
        assert (await validate_generation(uow.requirements, ready))[
            "actual_count"
        ] == len(atoms)
        saved = await uow.requirements.list_requirements(ready["generation_id"], 0, 100)
        assert [row["context"] for row in saved] == [["Ж" * 100_000]] * 20
    # Готовое поколение остаётся неизменным; повреждается только отдельная рабочая копия.
    async with factory() as uow:
        broken = {
            **ready,
            "generation_id": str(uuid4()),
            "state": "building",
            "active": False,
        }
        await uow.requirements.save_generation(broken)
        await uow.requirements.write_batch(broken["generation_id"], 0, atoms)
        await uow._session.execute(
            text(
                "UPDATE knowledge.normative_requirements SET ordinal=10000 WHERE generation_id=:g AND ordinal=:last"
            ),
            {"g": UUID(broken["generation_id"]), "last": len(atoms) - 1},
        )
        await uow.commit()
    async with factory() as uow:
        with pytest.raises(NormativeGenerationIntegrityError, match="ordinal"):
            await validate_generation(uow.requirements, broken)
        assert (await uow.requirements.active_generation(str(identifier)))[
            "generation_id"
        ] == ready["generation_id"]
