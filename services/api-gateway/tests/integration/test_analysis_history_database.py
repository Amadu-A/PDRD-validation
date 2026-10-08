# services/api-gateway/tests/integration/test_analysis_history_database.py

"""Реальный PostgreSQL: миграция индекса, изоляция владельцев и устойчивые страницы."""

import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pdrd_api_gateway.core.settings import Settings
from pdrd_api_gateway.domain.analysis_job import AnalysisJob
from pdrd_api_gateway.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_api_gateway.infrastructure.database.models import AnalysisJobModel
from pdrd_api_gateway.infrastructure.database.unit_of_work import SqlAlchemyUnitOfWork
from sqlalchemy import delete, text

ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(
        os.getenv("PDRD_RUN_DATABASE_TESTS") != "1",
        reason="Требуется изолированный PostgreSQL Gateway",
    ),
]


@pytest.mark.asyncio
async def test_history_migration_owner_filter_and_pagination():
    """Гостевые и чужие задания исключаются в БД до LIMIT/OFFSET."""
    settings = Settings(_env_file=None)
    assert settings.environment == "test" and settings.database.name.endswith(
        "_test"
    ), "Тест запрещён на рабочей БД"
    engine = build_async_engine(settings.database)
    factory = build_session_factory(engine)
    owner, other = uuid4(), uuid4()
    created = datetime(2026, 10, 6, tzinfo=UTC)
    rows = [
        replace(
            AnalysisJob.create(owner_user_id=owner, document_id=uuid4()),
            created_at=created + timedelta(minutes=index),
        )
        for index in range(5)
    ]
    rows += [
        AnalysisJob.create(owner_user_id=other),
        AnalysisJob.create(guest_access=True),
    ]
    try:
        async with engine.connect() as connection:
            migrations = ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG_PATH)))
            expected_head = migrations.get_current_head()
            assert expected_head is not None
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == expected_head
            )
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_indexes WHERE indexname='ix_analysis_jobs_owner_history'"
                    )
                )
                == 1
            )
        async with SqlAlchemyUnitOfWork(factory) as work:
            for job in rows:
                await work.analysis_jobs.add(job)
            await work.commit()
        async with SqlAlchemyUnitOfWork(factory) as work:
            first = await work.analysis_jobs.list_by_owner(
                owner_user_id=owner, limit=2, offset=0
            )
            second = await work.analysis_jobs.list_by_owner(
                owner_user_id=owner, limit=2, offset=2
            )
            third = await work.analysis_jobs.list_by_owner(
                owner_user_id=owner, limit=2, offset=4
            )
        assert [row.id for row in first + second + third] == [
            row.id for row in reversed(rows[:5])
        ]
        assert all(row.owner_user_id == owner for row in first + second + third)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                delete(AnalysisJobModel).where(
                    AnalysisJobModel.id.in_([row.id for row in rows])
                )
            )
        await engine.dispose()
