# tests/architecture/test_experience_migration.py

"""Архитектурные проверки миграций Experience Service.

Для чего нужен файл:
- проверяет генерацию SQL без подключения к PostgreSQL;
- контролирует порядок создания схемы и таблицы версий;
- закрепляет независимость миграций Experience Service;
- предотвращает повторение ошибки с незафиксированной транзакцией.

Последнее условие дополнительно проверяется интеграционными
тестами на настоящем изолированном PostgreSQL.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SERVICE = ROOT / "services" / "experience-service"

ALEMBIC_ENV = SERVICE / "alembic" / "env.py"


def test_experience_migration_is_offline_and_version_is_schema_isolated() -> None:
    """Проверяет SQL миграции без обращения к PostgreSQL.

    Схема experience должна создаваться раньше собственной
    таблицы версий Alembic и основных таблиц сервиса.
    """
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


def test_online_migration_uses_explicit_committing_transaction() -> None:
    """Запрещает выполнение online-миграций без фиксации транзакции.

    Ранее соединение открывалось через engine.connect().
    CREATE SCHEMA запускал неявную транзакцию, а закрытие
    соединения откатывало изменения.

    Теперь обязательным является engine.begin().
    """
    source = ALEMBIC_ENV.read_text(
        encoding="utf-8",
    )

    tree = ast.parse(
        source,
    )

    online = next(
        (
            node
            for node in tree.body
            if isinstance(
                node,
                ast.AsyncFunctionDef,
            )
            and node.name == "_run_migrations_online"
        ),
        None,
    )

    assert online is not None

    committing_contexts = []

    for node in ast.walk(online):
        if not isinstance(
            node,
            ast.AsyncWith,
        ):
            continue

        for item in node.items:
            expression = item.context_expr

            if not isinstance(
                expression,
                ast.Call,
            ):
                continue

            function = expression.func

            if (
                isinstance(function, ast.Attribute)
                and function.attr == "begin"
                and isinstance(function.value, ast.Name)
                and function.value.id == "engine"
            ):
                committing_contexts.append(
                    node,
                )

    assert committing_contexts, (
        "Online-миграция должна использовать явную транзакцию engine.begin()."
    )

    assert "await connection.run_sync(" in source
