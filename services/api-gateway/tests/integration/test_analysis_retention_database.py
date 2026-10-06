# services/api-gateway/tests/integration/test_analysis_retention_database.py

"""Реальный PostgreSQL: отбор 30/7 дней, каскад гостя и сохранение результата владельца."""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from functools import partial
from uuid import uuid4

import pytest
from pdrd_api_gateway.application.use_cases.cleanup_analysis_retention import (
    CleanupAnalysisRetention,
)
from pdrd_api_gateway.core.settings import Settings
from pdrd_api_gateway.domain.analysis_job import AnalysisJob, AnalysisJobStatus
from pdrd_api_gateway.domain.normative_snapshot import NormativeAnalysisSnapshot
from pdrd_api_gateway.domain.outbox import OutboxMessage
from pdrd_api_gateway.domain.technical_assignment import TechnicalAssignmentSnapshot
from pdrd_api_gateway.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_api_gateway.infrastructure.database.models import (
    AnalysisJobModel,
    OutboxMessageModel,
)
from pdrd_api_gateway.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from pdrd_api_gateway.infrastructure.storage.analysis_retention import (
    LocalAnalysisRetentionArtifacts,
)
from sqlalchemy import delete, select

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        os.getenv("PDRD_RUN_DATABASE_TESTS") != "1",
        reason="Требуется изолированный PostgreSQL Gateway",
    ),
]


@pytest.mark.asyncio
async def test_retention_repository_cascade_and_shared_assignment(tmp_path):
    """Точная граница включает старые задания, исключает свежие/активные и не удаляет владельца."""
    settings = Settings(_env_file=None)
    assert settings.environment == "test" and settings.database.name.endswith("_test")
    engine = build_async_engine(settings.database)
    factory = build_session_factory(engine)
    now = datetime(2026, 11, 6, tzinfo=UTC)
    owner, section = uuid4(), uuid4()
    assignment = TechnicalAssignmentSnapshot.create(
        analysis_document_id=uuid4(),
        section_id=section,
        source_file="tz.pdf",
        content=b"%PDF-TZ",
    )
    snapshot = NormativeAnalysisSnapshot.create(
        section_id=section,
        document_ids=(),
        system_prompt="Проверить",
        technical_assignment=assignment,
    )

    def create(owned, days, status=AnalysisJobStatus.COMPLETED):
        """Создаёт ряд с общим ТЗ."""
        return replace(
            AnalysisJob.create(
                owner_user_id=owner if owned else None,
                document_id=uuid4(),
                normative_snapshot=snapshot,
            ),
            created_at=now - timedelta(days=days),
            status=status,
        )

    expired_owner, expired_guest = create(True, 30), create(False, 7)
    fresh_owner, fresh_guest, active = (
        create(True, 29),
        create(False, 6),
        create(False, 90, AnalysisJobStatus.PROCESSING),
    )
    rows = [expired_owner, expired_guest, fresh_owner, fresh_guest, active]
    try:
        async with SqlAlchemyUnitOfWork(factory) as uow:
            for row in rows:
                await uow.analysis_jobs.add(row)
                folder = tmp_path / str(row.document_id)
                folder.mkdir()
                (folder / "pdf.bin").write_bytes(b"PDF")
                (folder / "result.json").write_text("{}", encoding="utf-8")
            await uow.commit()
        async with SqlAlchemyUnitOfWork(factory) as uow:
            await uow.outbox.add(
                OutboxMessage.analysis_requested(job_id=expired_guest.id)
            )
            await uow.commit()
        async with SqlAlchemyUnitOfWork(factory) as uow:
            candidates = await uow.analysis_jobs.list_retention_candidates(
                source_before=now - timedelta(days=30),
                guest_before=now - timedelta(days=7),
                limit=100,
            )
            assert {r.id for r in candidates} == {expired_owner.id, expired_guest.id}
            assert await uow.analysis_jobs.technical_assignment_retention(
                technical_assignment_id=assignment.technical_assignment_id,
                exclude_job_id=expired_guest.id,
                source_before=now - timedelta(days=30),
                guest_before=now - timedelta(days=7),
            ) == (True, True)
        cleaner = CleanupAnalysisRetention(
            partial(SqlAlchemyUnitOfWork, factory),
            LocalAnalysisRetentionArtifacts(root_path=tmp_path),
        )
        report = await cleaner.execute(now=now)
        assert (
            report.sources_removed == report.guests_removed == 1 and report.failed == 0
        )
        assert (await cleaner.execute(now=now)).selected == 0
        async with SqlAlchemyUnitOfWork(factory) as uow:
            kept = await uow.analysis_jobs.get(expired_owner.id)
            assert (
                kept.owner_user_id == owner and kept.source_artifacts_deleted_at == now
            )
            assert await uow.analysis_jobs.get(expired_guest.id) is None
        async with factory() as session:
            assert (
                await session.scalar(
                    select(OutboxMessageModel.id).where(
                        OutboxMessageModel.aggregate_id == expired_guest.id
                    )
                )
                is None
            )
        assert (tmp_path / str(expired_owner.document_id) / "result.json").exists()
        assert not (tmp_path / str(expired_owner.document_id) / "pdf.bin").exists()
        assert not (tmp_path / str(expired_guest.document_id)).exists()
        assert all(
            (tmp_path / str(r.document_id) / "pdf.bin").exists()
            for r in (fresh_owner, fresh_guest, active)
        )
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                delete(AnalysisJobModel).where(
                    AnalysisJobModel.id.in_([r.id for r in rows])
                )
            )
        await engine.dispose()
