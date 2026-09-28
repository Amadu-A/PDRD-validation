# tests/architecture/test_confirmed_area_migration.py

"""Offline-проверки отдельной миграции подтверждений Experience Service."""

import os
import subprocess
import sys
from pathlib import Path

from pdrd_experience_service.infrastructure.database.models import (
    AreaConfirmationEventModel,
    Base,
    ConfirmedAreaModel,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "services/experience-service"


def test_new_tables_have_independent_version_and_composite_event_key() -> None:
    """События не могут заменить предшествующую версию другого замечания."""
    assert {
        "experience.review_sessions",
        "experience.review_events",
        "experience.confirmed_areas",
        "experience.area_confirmation_events",
    } == set(Base.metadata.tables)

    assert [
        column.name for column in AreaConfirmationEventModel.__table__.primary_key
    ] == [
        "job_id",
        "finding_id",
        "revision",
    ]

    sql = str(
        CreateTable(
            ConfirmedAreaModel.__table__,
        ).compile(
            dialect=postgresql.dialect(),
        )
    )

    assert "experience.confirmed_areas" in sql
    assert "JSONB" in sql
    assert "ck_confirmed_areas_revision" in sql


def test_alembic_offline_includes_confirmation_tables() -> None:
    """Новая миграция должна следовать за старой без подключения к базе."""
    env = dict(os.environ)

    env["EXPERIENCE_SERVICE_DATABASE_URL"] = (
        "postgresql+asyncpg://offline:offline@127.0.0.1/never_connect"
    )
    env.pop(
        "EXPERIENCE_SERVICE_DATABASE__HOST",
        None,
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=SERVICE,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    ddl = result.stdout

    assert ddl.index("CREATE TABLE experience.review_sessions") < ddl.index(
        "CREATE TABLE experience.confirmed_areas"
    )

    assert "CREATE TABLE experience.area_confirmation_events" in ddl
    assert "20260928_0002" in ddl
