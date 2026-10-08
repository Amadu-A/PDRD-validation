# services/equipment-search-service/tests/integration/test_catalog_database.py

"""Настоящий PostgreSQL: версии snapshot, визуальные строки и история SSE."""

import asyncio
import os
from uuid import uuid4

import pytest
from pdrd_equipment_search_service.core.settings import Settings
from pdrd_equipment_search_service.domain.equipment import (
    DocumentInspection,
    DownloadedDocument,
    EquipmentIdentity,
)
from pdrd_equipment_search_service.infrastructure.postgres_catalog import (
    PostgresEquipmentCatalog,
)
from pdrd_equipment_search_service.infrastructure.postgres_jobs import (
    PostgresJobRepository,
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
