# services/equipment-search-service/tests/integration/test_document_assessment_database.py

"""Применимость справочника и история кандидатов сохраняются настоящим PostgreSQL."""

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
        reason="Нужен изолированный PostgreSQL Equipment.",
    ),
]


async def test_reference_metadata_and_candidate_checkpoint_survive_restart(tmp_path):
    """Справочник не получает вымышленного изготовителя, готовые документы не переписываются."""
    settings = Settings(_env_file=None)
    if (
        settings.database.host != "equipment-test-postgres"
        or settings.database.name != "pdrd_equipment_test"
    ):
        pytest.skip("Запуск только в штатном изолированном Equipment PostgreSQL.")
    engine = create_async_engine(settings.database.url())
    catalog = PostgresEquipmentCatalog(engine, tmp_path)
    host = f"{uuid4().hex}.example"
    url = f"https://{host}/standard.pdf"
    identity = EquipmentIdentity(
        "",
        f"Песок {uuid4().hex}",
        manufacturer_status="unknown",
        item_type="material",
        reference="ГОСТ 8736-85",
    )
    job_id = uuid4()
    repository = PostgresJobRepository(engine)
    try:
        await catalog.register_pending("", host, url, identity.model)
        saved = await catalog.save_document(
            identity,
            url,
            DownloadedDocument(b"%PDF-reference", url, "application/pdf"),
            DocumentInspection(
                True,
                pages=((1, "ГОСТ 8736-85 Песок"),),
                applicability="technical_reference",
                document_type="standard",
            ),
            "unverified_user_override",
        )
        found = await catalog.find_document(identity)
        loaded, content, _ = await catalog.load_document(saved.source_id)
        assert found.applicability == loaded.applicability == "technical_reference"
        assert loaded.document_type == "standard" and loaded.manufacturer == ""
        assert content == b"%PDF-reference"
        from pdrd_equipment_search_service.infrastructure.postgres_sources import (
            PostgresSourceManager,
        )

        manager = PostgresSourceManager(engine)
        sources = await manager.list_sources(query=host)
        assert (
            len(sources) == 1 and sources[0]["manufacturer"] == "Справочные источники"
        )
        await manager.update_source(
            manufacturer="Справочные источники",
            hostname=host,
            status="blocked",
            enabled=True,
            reason="Проверка запрета справочного сайта",
            actor="test",
        )
        assert await catalog.source_blocked(host)
        await repository.create_or_get(
            job_id, [{"manufacturer": "", "model": identity.model}], True
        )
        assert await repository.claim(job_id)
        results = [
            {
                "status": "incomplete",
                "candidates": [
                    {
                        "candidate_id": "EQC-1",
                        "applicability": "technical_reference",
                        "source_id": saved.source_id,
                    }
                ],
                "document": {"source_id": saved.source_id, "sha256": saved.sha256},
            }
        ]
        checkpoint = {
            "phase_usage": {"0": {"pages": 2, "extractions": 1}},
            "inflight": {"index": 0},
        }
        await repository.checkpoint(job_id, results, checkpoint)
        await repository.recover_interrupted()
        recovered = await repository.get(job_id)
        assert recovered["results"] == results and recovered["checkpoint"] == checkpoint
        async with engine.connect() as connection:
            assert not await connection.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM equipment_search.manufacturers WHERE normalized_name = '')"
                )
            )
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM equipment_search.equipment_jobs WHERE job_id = :id"),
                {"id": job_id},
            )
            for table in ("documents", "source_audit"):
                await connection.execute(
                    text(
                        f"DELETE FROM equipment_search.{table} WHERE source_domain_id IN "
                        "(SELECT id FROM equipment_search.source_domains WHERE hostname = :host)"
                    ),
                    {"host": host},
                )
            await connection.execute(
                text(
                    "DELETE FROM equipment_search.source_domains WHERE hostname = :host"
                ),
                {"host": host},
            )
        await engine.dispose()


async def test_legacy_job_idempotency_does_not_rewrite_saved_inputs():
    """Новые поля классификации не ломают повтор запроса задания прежней версии."""
    from dataclasses import asdict

    settings = Settings(_env_file=None)
    if (
        settings.database.host != "equipment-test-postgres"
        or settings.database.name != "pdrd_equipment_test"
    ):
        pytest.skip("Запуск только в штатном изолированном Equipment PostgreSQL.")
    engine = create_async_engine(settings.database.url())
    repository = PostgresJobRepository(engine)
    job_id = uuid4()
    current = {**asdict(EquipmentIdentity("Legacy maker", "M-20")), "properties": []}
    old = {
        key: value
        for key, value in current.items()
        if key not in {"item_type", "name", "reference"}
    }
    try:
        await repository.create_or_get(job_id, [old], False)
        repeated = await repository.create_or_get(job_id, [current], False)
        assert repeated["identities"] == [old]
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM equipment_search.equipment_jobs WHERE job_id = :id"),
                {"id": job_id},
            )
        await engine.dispose()
