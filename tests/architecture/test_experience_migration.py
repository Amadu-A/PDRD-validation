# tests/architecture/test_experience_migration.py

"""Experience Alembic must remain separate and create its schema before versioning."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = (
    Path(
        __file__,
    )
    .resolve()
    .parents[2]
)

SERVICE = ROOT / "services" / "experience-service"


def test_experience_migration_is_offline_and_version_is_schema_isolated() -> None:
    """Compile DDL without contacting production or a test database."""
    env = dict(
        os.environ,
    )

    env["EXPERIENCE_SERVICE_DATABASE_URL"] = (
        "postgresql+asyncpg://offline:offline@127.0.0.1/never_connect"
    )

    command = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        "alembic.ini",
        "upgrade",
        "head",
        "--sql",
    ]

    result = subprocess.run(
        command,
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

    assert ddl.index("CREATE SCHEMA IF NOT EXISTS experience") < ddl.index(
        "CREATE TABLE experience.alembic_version_experience"
    )

    assert "CREATE TABLE experience.review_sessions" in ddl

    assert "CREATE TABLE experience.review_events" in ddl

    assert "experience.review_sessions" in ddl
