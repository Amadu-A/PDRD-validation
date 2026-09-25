# services/experience-service/tests/integration/test_review_database.py

"""Optional PostgreSQL integration: two writers must not silently overwrite review."""

import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pdrd_experience_service.domain.review import (
    Decision,
    OriginalFinding,
    ReviewConflictError,
    ReviewSession,
)
from pdrd_experience_service.infrastructure.database.repository import (
    SqlAlchemyReviewRepository,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

pytestmark = pytest.mark.database


@pytest.mark.asyncio
async def test_insert_cas_and_complete_audit_on_postgres() -> None:
    """Verify transaction boundaries against a real disposable PostgreSQL DB."""
    if os.environ.get("PDRD_RUN_DATABASE_TESTS") != "1":
        pytest.skip("Requires PDRD_RUN_DATABASE_TESTS=1 and disposable PostgreSQL")

    url = os.environ.get("EXPERIENCE_SERVICE_TEST_DATABASE_URL")

    if not url:
        pytest.skip("Requires EXPERIENCE_SERVICE_TEST_DATABASE_URL")

    engine = create_async_engine(
        url,
    )

    job = UUID(int=599)

    now = datetime.now(UTC)

    repo = SqlAlchemyReviewRepository(
        async_sessionmaker(
            engine,
            expire_on_commit=False,
        )
    )

    try:
        async with engine.begin() as connection:
            # The migration must have been applied to this disposable DB first.
            await connection.execute(
                text("DELETE FROM experience.review_sessions WHERE job_id = :id"),
                {
                    "id": job,
                },
            )

        session = ReviewSession.open(
            job_id=job,
            document_id=UUID(int=600),
            source_filename="test.pdf",
            source_sha256="b" * 64,
            originals=(
                OriginalFinding(
                    "f1",
                    1,
                    "Замечание",
                ),
            ),
            rendered_pages=(1,),
            actor="test:1",
            at=now,
        )

        await repo.insert(
            session,
        )

        loaded = await repo.load(
            job,
        )

        assert loaded == session

        updated = session.decide(
            finding_id="f1",
            decision=Decision.ACCEPTED,
            actor="test:2",
            at=now,
            expected_revision=0,
        )

        await repo.update(
            updated,
            expected_revision=0,
        )

        with pytest.raises(ReviewConflictError):
            await repo.update(
                updated,
                expected_revision=0,
            )

        assert await repo.load(job) == updated

    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM experience.review_sessions WHERE job_id = :id"),
                {
                    "id": job,
                },
            )

        await engine.dispose()
