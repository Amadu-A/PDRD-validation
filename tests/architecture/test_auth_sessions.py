# tests/architecture/test_auth_sessions.py

"""Проверяет границу схемы auth, миграцию и безопасность изолированного теста."""

import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
AUTH_SERVICE = ROOT / "services" / "auth-service"
AUTH_COMPOSE = ROOT / "ops" / "compose.auth-test.yaml"


def test_auth_migration_offline_owns_only_auth_schema() -> None:
    """Генерирует SQL без сети и контролирует порядок создания схемы."""
    environment = dict(os.environ)
    environment["AUTH_SERVICE_DATABASE_URL"] = (
        "postgresql+asyncpg://offline:offline@127.0.0.1/never_connect"
    )
    for name in tuple(environment):
        if name.upper().startswith("AUTH_SERVICE_DATABASE__"):
            environment.pop(name)
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
        cwd=AUTH_SERVICE,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    ddl = result.stdout
    assert ddl.index("CREATE SCHEMA IF NOT EXISTS auth") < ddl.index(
        "CREATE TABLE auth.alembic_version_auth"
    )
    assert "CREATE TABLE auth.sessions" in ddl
    assert "token_hash VARCHAR(64) NOT NULL" in ddl
    assert "CREATE TABLE users." not in ddl
    assert "CREATE TABLE experience." not in ddl


def test_auth_table_has_no_raw_token_or_ad_password() -> None:
    """Схема БД содержит только хеш и сроки серверной сессии."""
    from pdrd_auth_service.infrastructure.database.models import SessionModel

    columns = set(SessionModel.__table__.columns.keys())
    assert "token_hash" in columns
    assert not columns.intersection(
        {"token", "session_token", "password", "ad_password"}
    )
    assert SessionModel.__table__.schema == "auth"


def test_auth_database_runner_is_isolated() -> None:
    """Интеграционный Compose не обращается к рабочему стеку и его секретам."""
    compose = yaml.safe_load(AUTH_COMPOSE.read_text(encoding="utf-8"))
    services = compose["services"]
    postgres = services["auth-test-postgres"]
    runner = services["auth-test-runner"]
    assert compose["name"] == "pdrd-auth-service-test"
    assert set(services) == {"auth-test-postgres", "auth-test-runner"}
    assert compose["networks"]["auth-test-only"]["internal"] is True
    assert "/var/lib/postgresql/data" in postgres["tmpfs"]
    assert "volumes" not in compose
    for service in services.values():
        assert "ports" not in service
        assert "env_file" not in service
        assert service["networks"] == ["auth-test-only"]
    assert runner["environment"]["AUTH_SERVICE_ENABLED"] == "false"
    assert runner["environment"]["PDRD_RUN_DATABASE_TESTS"] == "1"
    assert (
        "auth-test-postgres" in runner["environment"]["AUTH_SERVICE_TEST_DATABASE_URL"]
    )
    commands = "\n".join(runner["command"])
    assert "alembic -c alembic.ini upgrade head" in commands
    assert "alembic -c alembic.ini current --check-heads" in commands
    assert "services/auth-service/tests/integration" in commands
