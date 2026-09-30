# services/experience-service/tests/unit/test_database_configuration.py

"""Модульные проверки конфигурации PostgreSQL для Experience Service.

Назначение файла:
- проверять порядок загрузки .env.example, .env и process environment;
- запрещать использование примерного пароля активным сервисом;
- проверять диапазон параметров подключения;
- контролировать безопасное формирование PostgreSQL URL;
- проверять создание engine и фабрики сессий без сетевого подключения.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from pdrd_experience_service.core.settings import (
    DatabaseSettings,
    Settings,
)
from pdrd_experience_service.infrastructure.database.engine import (
    build_async_engine,
    build_database_url,
    build_session_factory,
)
from pydantic import ValidationError


def test_default_configuration_is_disabled() -> None:
    """Сервис без явного включения не должен обращаться к рабочей базе."""
    settings = Settings(
        _env_file=None,
    )

    assert settings.enabled is False
    assert settings.database.host == "postgres"
    assert settings.database.port == 5432


def test_layered_files_and_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Приватный файл переопределяет baseline, а переменные процесса — оба."""
    example = tmp_path / ".env.example"
    private = tmp_path / ".env"

    example.write_text(
        "EXPERIENCE_SERVICE_DATABASE__HOST=baseline\n"
        "EXPERIENCE_SERVICE_DATABASE__POOL_SIZE=4\n"
        "EXPERIENCE_SERVICE_DATABASE__PASSWORD=change-me\n",
        encoding="utf-8",
    )

    private.write_text(
        "EXPERIENCE_SERVICE_DATABASE__HOST=private\n"
        "EXPERIENCE_SERVICE_DATABASE__PASSWORD=secret-from-file\n",
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=(
            example,
            private,
        ),
    )

    assert settings.database.host == "private"
    assert settings.database.pool_size == 4

    assert settings.database.password.get_secret_value() == "secret-from-file"

    monkeypatch.setenv(
        "EXPERIENCE_SERVICE_DATABASE__HOST",
        "process",
    )

    assert (
        Settings(
            _env_file=(
                example,
                private,
            )
        ).database.host
        == "process"
    )


def test_prod_or_enabled_rejects_example_password() -> None:
    """Активный сервис нельзя запускать с паролем из .env.example."""
    with pytest.raises(
        ValidationError,
        match="настоящий пароль",
    ):
        Settings(
            _env_file=None,
            environment="prod",
        )

    with pytest.raises(
        ValidationError,
        match="настоящий пароль",
    ):
        Settings(
            _env_file=None,
            enabled=True,
        )

    active = Settings(
        _env_file=None,
        enabled=True,
        database=DatabaseSettings(
            password="private-real-password",
        ),
    )

    assert active.enabled


@pytest.mark.parametrize(
    "port",
    [
        0,
        65536,
    ],
)
def test_invalid_database_port(
    port: int,
) -> None:
    """Недопустимые TCP-порты отклоняются до создания engine."""
    with pytest.raises(
        ValidationError,
    ):
        DatabaseSettings(
            port=port,
        )


def test_password_is_escaped_and_masked() -> None:
    """Специальные символы пароля не нарушают DSN и не попадают в repr."""
    database = DatabaseSettings(
        host="test-postgres",
        user="review_user",
        name="review_test",
        password="private:@#/word",
    )

    url = build_database_url(
        database,
    )

    assert url.drivername == "postgresql+asyncpg"
    assert url.password == "private:@#/word"

    assert "private:@#/word" not in repr(url)
    assert "private:@#/word" not in repr(database)

    assert "private%3A%40%23%2Fword" in url.render_as_string(
        hide_password=False,
    )


def test_engine_is_configured_without_connecting() -> None:
    """Создание engine передаёт настройки пула, не выполняя SQL."""
    settings = DatabaseSettings(
        password="test-only",
        pool_size=3,
        max_overflow=7,
        pool_timeout_seconds=11,
        connect_timeout_seconds=4,
    )

    marker = object()

    with patch(
        "pdrd_experience_service.infrastructure.database.engine.create_async_engine",
        return_value=marker,
    ) as create:
        result = build_async_engine(
            settings,
        )

    assert result is marker

    args, kwargs = create.call_args

    assert args[0].database == settings.name
    assert kwargs["pool_size"] == 3
    assert kwargs["max_overflow"] == 7
    assert kwargs["pool_timeout"] == 11
    assert kwargs["connect_args"] == {
        "timeout": 4,
    }
    assert kwargs["pool_pre_ping"] is True


def test_session_factory_uses_independent_transactions() -> None:
    """Composition root получает фабрику независимых SQLAlchemy-сессий."""
    engine = object()
    factory = object()

    with patch(
        "pdrd_experience_service.infrastructure.database.engine.async_sessionmaker",
        return_value=factory,
    ) as builder:
        assert (
            build_session_factory(
                engine,  # type: ignore[arg-type]
            )
            is factory
        )

    builder.assert_called_once_with(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )
