# services/experience-service/tests/integration/test_equipment_update_database.py

"""Реальный PostgreSQL сохраняет пакет EQ, аудит и защиту утверждённого Review."""

import asyncio
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pdrd_experience_service.application.use_cases.equipment_update import (
    import_equipment_update,
)
from pdrd_experience_service.domain.review import (
    Decision,
    OriginalFinding,
    ReviewConflictError,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.database.models import ReviewSessionModel
from pdrd_experience_service.infrastructure.database.repository import (
    SqlAlchemyReviewRepository,
)
from sqlalchemy import delete
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


async def test_equipment_update_round_trip_atomic_conflict_and_approved_guard():
    """Несколько EQ-находок пишутся одним CAS, после пересоздания репозитория аудит полон."""
    if os.getenv("PDRD_RUN_DATABASE_TESTS") != "1":
        pytest.skip("Нужен изолированный PostgreSQL Experience.")
    raw = os.environ["EXPERIENCE_SERVICE_TEST_DATABASE_URL"]
    url = make_url(raw)
    assert (url.host, url.database, url.username) == (
        "experience-test-postgres",
        "pdrd_experience_test",
        "experience_test",
    )
    assert os.environ["EXPERIENCE_SERVICE_DATABASE_URL"] == raw
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repository = SqlAlchemyReviewRepository(factory)
    current = ReviewSession.open(
        job_id=uuid4(),
        document_id=uuid4(),
        source_filename="drawing.pdf",
        source_sha256="a" * 64,
        originals=(OriginalFinding("N-existing", 1, "Прежнее замечание"),),
        rendered_pages=(1, 2),
        actor="integration:engineer",
        at=datetime.now(UTC),
    )

    def finding(identifier):
        """Возвращает серверный EQ-кандидат с неизменяемым источником."""
        return {
            "finding_id": identifier,
            "page": 2,
            "status": "confirmed",
            "comment": "Напряжение вне диапазона",
            "equipment_documentation_basis_sources": [
                {
                    "source_id": "EQ-" + "b" * 32,
                    "sha256": "c" * 64,
                    "page": 3,
                    "snippet": "РИП-12 исп. 51: 12 В",
                }
            ],
        }

    try:
        await repository.insert(current)
        accepted = current.decide(
            finding_id="N-existing",
            decision=Decision.ACCEPTED,
            actor="integration:engineer",
            at=datetime.now(UTC),
            expected_revision=0,
        )
        await repository.update(accepted, expected_revision=0)
        updated = await import_equipment_update(
            repository,
            job_id=current.job_id,
            actor="integration:engineer",
            expected_revision=1,
            equipment_revision=25,
            findings=[finding("EQ-first"), finding("EQ-second")],
        )
        recreated = SqlAlchemyReviewRepository(factory)
        restored = await recreated.load(current.job_id)
        assert restored == updated
        assert restored.findings[0] == accepted.findings[0]
        assert [event.session_revision for event in restored.history] == [0, 1, 2]
        assert len(restored.history[-1].added_findings) == 2
        assert restored.history[-1].equipment_revision == 25
        results = await asyncio.gather(
            *(
                import_equipment_update(
                    recreated,
                    job_id=current.job_id,
                    actor="integration:engineer",
                    expected_revision=2,
                    equipment_revision=26,
                    findings=[finding(f"EQ-concurrent-{index}")],
                )
                for index in range(2)
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, ReviewConflictError) for result in results) == 1
        restored = await recreated.load(current.job_id)
        assert restored.revision == 3
        assert len(restored.findings) == 4
        for row in restored.findings:
            if row.decision is Decision.PENDING:
                decided = restored.decide(
                    finding_id=row.finding_id,
                    decision=Decision.ACCEPTED,
                    actor="integration:engineer",
                    at=datetime.now(UTC),
                    expected_revision=restored.revision,
                )
                await recreated.update(decided, expected_revision=restored.revision)
                restored = decided
        approved = restored.approve(
            actor="integration:engineer",
            at=datetime.now(UTC),
            expected_revision=restored.revision,
        )
        await recreated.update(approved, expected_revision=restored.revision)
        with pytest.raises(ReviewConflictError):
            await import_equipment_update(
                recreated,
                job_id=current.job_id,
                actor="integration:engineer",
                expected_revision=approved.revision,
                equipment_revision=27,
                findings=[finding("EQ-after-approval")],
            )
        assert await recreated.load(current.job_id) == approved
    finally:
        async with factory() as database, database.begin():
            await database.execute(
                delete(ReviewSessionModel).where(
                    ReviewSessionModel.job_id == current.job_id
                )
            )
        await engine.dispose()
