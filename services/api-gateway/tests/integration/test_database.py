# services/api-gateway/tests/integration/test_database.py

"""Integration-тесты API Gateway с настоящим PostgreSQL."""

import os
from functools import partial
from uuid import uuid4

import pytest
from pdrd_api_gateway.application.use_cases.create_analysis_job import (
    CreateAnalysisJob,
)
from pdrd_api_gateway.core.settings import Settings
from pdrd_api_gateway.domain.analysis_job import AnalysisJobStatus
from pdrd_api_gateway.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_api_gateway.infrastructure.database.health import (
    DatabaseReadinessProbe,
)
from pdrd_api_gateway.infrastructure.database.models import (
    AnalysisJobModel,
)
from pdrd_api_gateway.infrastructure.database.unit_of_work import (
    SqlAlchemyUnitOfWork,
)
from sqlalchemy import delete

RUN_DATABASE_TESTS = (
    os.getenv(
        "PDRD_RUN_DATABASE_TESTS",
        "0",
    )
    == "1"
)

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        not RUN_DATABASE_TESTS,
        reason=("Database integration tests require PDRD_RUN_DATABASE_TESTS=1."),
    ),
]


async def test_database_health_job_and_outbox() -> None:
    """Проверяет PostgreSQL, UoW, job и transactional outbox."""
    settings = Settings(
        _env_file=None,
    )

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    health_probe = DatabaseReadinessProbe(
        engine=engine,
        timeout_seconds=(settings.database.health_timeout_seconds),
    )

    unit_of_work_factory = partial(
        SqlAlchemyUnitOfWork,
        session_factory,
    )

    use_case = CreateAnalysisJob(
        unit_of_work_factory=unit_of_work_factory,
    )

    created_job = None
    document_id = uuid4()

    try:
        assert await health_probe.is_ready() is True

        created_job = await use_case.execute(
            document_id=document_id,
        )

        async with unit_of_work_factory() as unit_of_work:
            loaded_job = await unit_of_work.analysis_jobs.get(
                created_job.id,
            )

            pending_messages = await unit_of_work.outbox.get_pending(
                limit=100,
            )

        assert loaded_job is not None
        assert loaded_job.id == created_job.id
        assert loaded_job.document_id == document_id
        assert loaded_job.status is AnalysisJobStatus.PENDING
        assert loaded_job.attempt_count == 0

        job_messages = [
            message
            for message in pending_messages
            if message.aggregate_id == created_job.id
        ]

        assert len(job_messages) == 1
        assert job_messages[0].payload == {
            "job_id": str(created_job.id),
        }
    finally:
        if created_job is not None:
            async with session_factory() as session:
                await session.execute(
                    delete(
                        AnalysisJobModel,
                    ).where(AnalysisJobModel.id == created_job.id)
                )

                await session.commit()

        await engine.dispose()
