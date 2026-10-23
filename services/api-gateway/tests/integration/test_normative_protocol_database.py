# services/api-gateway/tests/integration/test_normative_protocol_database.py

"""Реальный изолированный PostgreSQL: scope, row lock отмены и публикация архива."""

import asyncio
import hashlib
import os
from functools import partial
from uuid import uuid4

import pytest
from pdrd_analysis_service.domain.normative_protocol import (
    canonical_json,
    protocol_archive,
)
from pdrd_api_gateway.application.normative_protocol import ManageNormativeProtocol
from pdrd_api_gateway.application.ports.normative_protocol import (
    NormativeProtocolError,
    NormativeProtocolNotFound,
)
from pdrd_api_gateway.core.settings import Settings
from pdrd_api_gateway.domain.analysis_job import AnalysisJob
from pdrd_api_gateway.domain.normative_snapshot import NormativeAnalysisSnapshot
from pdrd_api_gateway.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_api_gateway.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from pdrd_api_gateway.infrastructure.storage.normative_protocol import (
    LocalNormativeProtocolStore,
)

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        os.getenv("PDRD_RUN_DATABASE_TESTS") != "1",
        reason="Требуется отдельный PostgreSQL Gateway",
    ),
]


@pytest.mark.parametrize("cancel", [False, True])
async def test_postgres_protocol_publication_respects_snapshot_and_cancel_row_lock(
    tmp_path, cancel
):
    """Отмена, захватившая блокировку первой, не позволяет опубликовать успешную стадию."""
    settings = Settings(_env_file=None)
    assert settings.environment == "test"
    assert (
        settings.database.host == "gateway-test-postgres"
        and settings.database.name == "pdrd_gateway_test"
    )
    engine = build_async_engine(settings.database)
    factory = partial(SqlAlchemyUnitOfWork, build_session_factory(engine))
    section, normative = uuid4(), uuid4()
    job = AnalysisJob.create(
        document_id=uuid4(),
        normative_snapshot=NormativeAnalysisSnapshot(
            section, (normative,), "Учебный snapshot"
        ),
    )
    store = LocalNormativeProtocolStore(tmp_path)
    directory = tmp_path / str(job.document_id)
    directory.mkdir()
    (directory / "request.json").write_text("{}", encoding="utf-8")
    manager = ManageNormativeProtocol(factory, store)
    archive = protocol_archive(
        document_id=str(job.document_id),
        section_id=str(section),
        document_ids=[str(normative)],
        diagnostics={"checks": [], "selection": []},
        sources=[],
    )
    raw = canonical_json(archive)
    protocol_id = hashlib.sha256(raw).hexdigest()
    manifest = {
        "part_count": 1,
        "bytes": len(raw),
        "section_id": str(section),
        "document_ids": [str(normative)],
        "counts": archive["counts"],
    }
    try:
        async with factory() as unit:
            await unit.analysis_jobs.add(job)
            await unit.commit()
        await store.put_part(job.document_id, protocol_id, 0, raw)
        if cancel:
            async with factory() as unit:
                locked = await unit.analysis_jobs.get_for_update(job.id)
                task = asyncio.create_task(
                    manager.publish(job.document_id, protocol_id, manifest)
                )
                await asyncio.sleep(0.1)
                assert not task.done()
                locked.mark_cancelled()
                await unit.analysis_jobs.update(locked)
                await unit.commit()
            with pytest.raises(NormativeProtocolError, match="analysis_cancelled"):
                await task
            with pytest.raises(NormativeProtocolNotFound):
                await store.read(job.document_id, protocol_id)
        else:
            published = await manager.publish(job.document_id, protocol_id, manifest)
            assert published["job_id"] == str(job.id) and published["state"] == "ready"
            await manager.validate_result(
                job.id,
                job.document_id,
                {"metrics": {"requirement_plan": {"protocol": published}}},
            )
            restarted = ManageNormativeProtocol(
                factory, LocalNormativeProtocolStore(tmp_path)
            )
            assert (
                await restarted.read(job.id, protocol_id, "manifest", 0, 1)
            ) == published
            with pytest.raises(NormativeProtocolNotFound):
                await restarted.read(uuid4(), protocol_id, "manifest", 0, 1)
    finally:
        async with factory() as unit:
            await unit.analysis_jobs.delete(job.id)
            await unit.commit()
        await engine.dispose()
