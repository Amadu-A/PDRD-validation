# services/equipment-search-service/tests/integration/test_catalog_database.py

"""Настоящий PostgreSQL: версии snapshot, визуальные строки и история SSE."""

import asyncio
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pdrd_equipment_search_service.application.jobs import SearchJobService
from pdrd_equipment_search_service.core.settings import Settings
from pdrd_equipment_search_service.domain.equipment import (
    DocumentInspection,
    DownloadedDocument,
    EquipmentIdentity,
    ResolveResult,
)
from pdrd_equipment_search_service.infrastructure.postgres_catalog import (
    PostgresEquipmentCatalog,
)
from pdrd_equipment_search_service.infrastructure.postgres_jobs import (
    PostgresJobRepository,
)
from pdrd_equipment_search_service.infrastructure.postgres_sources import (
    PostgresSourceManager,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        os.getenv("PDRD_RUN_DATABASE_TESTS") != "1",
        reason="Нужен настоящий PostgreSQL с миграциями Equipment Search.",
    ),
]


def _isolated_engine():
    """Не допускает запись тестов в рабочую БД проекта."""
    settings = Settings(_env_file=None)
    if (
        settings.database.host != "equipment-test-postgres"
        or settings.database.name != "pdrd_equipment_test"
    ):
        pytest.skip(
            "Equipment DB тесты запускаются через ops/compose.equipment-test.yaml."
        )
    return create_async_engine(settings.database.url())


async def test_snapshot_versions_keep_bytes_and_visual_transcripts(tmp_path) -> None:
    """Другой SHA создаёт новую версию, прежние байты и строки доступны."""
    engine = _isolated_engine()
    catalog = PostgresEquipmentCatalog(engine, tmp_path)
    manufacturer = "Test-" + uuid4().hex
    identity = EquipmentIdentity(manufacturer, "NXB-63")
    url = "https://maker.example/manual.pdf"
    raw = ({"page": 2, "snippet": "NXB-63 230 V"},)
    inspection = DocumentInspection(
        True, revision="1", pages=((2, ""),), vision_facts=raw
    )
    try:
        await catalog.register_pending(
            manufacturer, "maker.example", url, identity.model
        )
        first = await catalog.save_document(
            identity,
            url,
            DownloadedDocument(b"%PDF-first", url, "application/pdf"),
            inspection,
            "unverified_user_override",
        )
        second = await catalog.save_document(
            identity,
            url,
            DownloadedDocument(b"%PDF-second", url, "application/pdf"),
            inspection,
            "unverified_user_override",
        )
        assert first.source_id != second.source_id
        assert (await catalog.find_document(identity)).revision_ambiguous is True
        assert await catalog.document_vision_facts(first.source_id) == raw
        loaded, content, media = await catalog.load_document(first.source_id)
        assert (
            loaded.sha256 == first.sha256
            and content == b"%PDF-first"
            and media == "application/pdf"
        )
    finally:
        async with engine.begin() as connection:
            for table in (
                "documents",
                "source_audit",
                "source_domains",
                "manufacturers",
            ):
                if table == "source_audit":
                    query = (
                        "DELETE FROM equipment_search.source_audit WHERE source_domain_id IN "
                        "(SELECT id FROM equipment_search.source_domains WHERE manufacturer_key = :key)"
                    )
                else:
                    key = (
                        "normalized_name"
                        if table == "manufacturers"
                        else "manufacturer_key"
                    )
                    query = f"DELETE FROM equipment_search.{table} WHERE {key} = :key"
                await connection.execute(text(query), {"key": manufacturer.casefold()})
        await engine.dispose()


async def test_job_event_sequence_is_atomic_and_replayable() -> None:
    """Конкурентная запись выдаёт непрерывные ID и replay после курсора."""
    engine = _isolated_engine()
    repository = PostgresJobRepository(engine)
    job_id = uuid4()
    payload = [{"manufacturer": "IEK", "model": "KM20"}]
    try:
        await repository.create_or_get(job_id, payload, False)
        assert await repository.claim(job_id)
        assert not await repository.claim(job_id)
        ids = await asyncio.gather(
            *(
                repository.append_event(job_id, "model_started", f"Событие {index}")
                for index in range(20)
            )
        )
        assert sorted(ids) == list(range(1, 21))
        events = await repository.events_after(job_id, 10)
        assert [event["sequence"] for event in events] == list(range(11, 21))
        with pytest.raises(ValueError):
            await repository.create_or_get(job_id, payload, True)
        await repository.finish(job_id, "completed", [{"metrics": {"vision_calls": 0}}])
        state = await repository.get(job_id)
        assert state["results"][0]["metrics"]["vision_calls"] == 0
        assert not await repository.cancel(job_id)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM equipment_search.equipment_jobs WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            )
        await engine.dispose()


@pytest.mark.parametrize("properties", [(), ("voltage",)])
@pytest.mark.parametrize("allow_unverified", [False, True])
async def test_job_service_start_and_replay_use_real_jsonb(
    properties, allow_unverified
):
    """Сценарий запуска переживает JSONB; изменённая команда сохраняет конфликт."""
    engine = _isolated_engine()
    repository = PostgresJobRepository(engine)
    identity = EquipmentIdentity(
        "Рубеж", "РИП-12", "исп. 51", confidence=0.95, properties=properties
    )
    resolver = SimpleNamespace(
        execute=AsyncMock(return_value=ResolveResult(identity, "not_found"))
    )
    service = SearchJobService(repository, resolver)
    job_id = uuid4()
    try:
        created = await service.start(job_id, [identity], allow_unverified)
        assert created["status"] == "queued"
        expected = [
            {
                "manufacturer": "Рубеж",
                "model": "РИП-12",
                "variant": "исп. 51",
                "status": "resolved",
                "confidence": 0.95,
                "properties": list(properties),
            }
        ]
        assert created["identities"] == expected
        assert created["allow_unverified"] is allow_unverified
        repeated = await service.start(job_id, [identity], allow_unverified)
        assert repeated["identities"] == expected
        await asyncio.gather(*service._tasks.values())
        finished = await repository.get(job_id)
        assert finished["status"] == "completed"
        assert finished["identities"] == expected
        resolver.execute.assert_awaited_once()
        assert (
            resolver.execute.await_args.kwargs["allow_unverified"] is allow_unverified
        )
        assert (await service.start(job_id, [identity], allow_unverified))[
            "status"
        ] == "completed"
        resolver.execute.assert_awaited_once()
        for changed in (
            replace(identity, properties=("power",)),
            replace(identity, variant="исп. 52"),
            replace(identity, model="РИП-24"),
        ):
            with pytest.raises(ValueError, match="другими параметрами"):
                await service.start(job_id, [changed], allow_unverified)
        with pytest.raises(ValueError, match="другими параметрами"):
            await service.start(job_id, [identity], not allow_unverified)
        assert (await repository.get(job_id))["identities"] == expected
    finally:
        await service.shutdown()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM equipment_search.equipment_jobs WHERE job_id = :job_id"
                ),
                {"job_id": job_id},
            )
        await engine.dispose()


async def test_original_trusted_registry_is_visible_by_manufacturer(tmp_path):
    """Все семь исходных сайтов доступны в БД и в каталоге производителя."""
    engine = _isolated_engine()
    catalog = PostgresEquipmentCatalog(engine, tmp_path)
    expected = {
        "Segnetics": "segnetics.com",
        "CHINT": "chint.ru",
        "Shenler": "shenler.com",
        "IEK": "iek.ru",
        "TDM": "tdme.ru",
        "CNC": "cncele.com",
        "MeanWell": "meanwell.com",
    }
    try:
        for maker, hostname in expected.items():
            source = await catalog.source_for(maker, hostname)
            assert source.status.value == "trusted"
            assert source.enabled
            assert not source.allow_http
            assert hostname in await catalog.trusted_hosts(maker)
            assert await catalog.source_for(maker, "unknown." + hostname) is None
        manager = PostgresSourceManager(engine)
        all_sources = await manager.list_sources()
        confirmed = await manager.list_sources("trusted")
        assert set(expected.values()).issubset({row["hostname"] for row in all_sources})
        assert set(expected.values()).issubset({row["hostname"] for row in confirmed})
        filtered = await manager.list_sources("trusted", "chint.ru")
        assert [row["hostname"] for row in filtered] == ["chint.ru"]
        assert filtered[0]["manufacturer"] == "CHINT"
        assert (
            await manager.list_sources(None, "definitely-absent-domain.invalid") == []
        )
    finally:
        await engine.dispose()


async def test_registry_upgrade_preserves_manual_decisions_and_audit(monkeypatch):
    """Повтор bootstrap не снимает блокировку и не дублирует историю."""
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    filename = (
        Path(__file__).parents[2]
        / "alembic/versions/20261009_0003_original_trusted_sources.py"
    )
    specification = spec_from_file_location("original_eq_registry", filename)
    migration = module_from_spec(specification)
    specification.loader.exec_module(migration)
    engine = _isolated_engine()
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(
                    text(
                        "UPDATE equipment_search.source_domains "
                        "SET status = 'blocked', enabled = false, allow_http = true, "
                        "updated_by = 'manual-test' WHERE hostname = 'chint.ru'"
                    )
                )

                async def count_audit():
                    """Читает число событий до и после повторной миграции."""
                    return await connection.scalar(
                        text("SELECT count(*) FROM equipment_search.source_audit")
                    )

                before = await count_audit()

                def rerun(sync_connection):
                    """Исполняет реальную миграцию в откатываемой транзакции тестовой БД."""
                    monkeypatch.setattr(
                        migration.op, "get_bind", lambda: sync_connection
                    )
                    migration.upgrade()
                    migration.upgrade()

                await connection.run_sync(rerun)
                assert await count_audit() == before
                row = (
                    await connection.execute(
                        text(
                            "SELECT status, enabled, allow_http, updated_by FROM equipment_search.source_domains "
                            "WHERE hostname = 'chint.ru'"
                        )
                    )
                ).one()
                assert tuple(row) == ("blocked", False, True, "manual-test")
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
