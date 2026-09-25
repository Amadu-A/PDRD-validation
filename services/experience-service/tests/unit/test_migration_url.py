# services/experience-service/tests/unit/test_migration_url.py

"""Модульные проверки согласованности PostgreSQL и Alembic.

Назначение:
- проверять совместимый явный URL тестовых миграций;
- использовать стандартные Pydantic Settings рабочего сервиса;
- предотвращать конфликт двух способов конфигурации;
- запрещать неявную миграцию выключенного сервиса;
- проверять корректность паролей со специальными символами.
"""

import pytest
from pdrd_experience_service.core.settings import (
    DatabaseSettings,
    Settings,
)
from pdrd_experience_service.infrastructure.database.migration_url import (
    resolve_migration_url,
)


def test_explicit_test_url_is_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Изолированные DB-тесты сохраняют существующий способ подключения."""
    monkeypatch.delenv(
        "EXPERIENCE_SERVICE_DATABASE__HOST",
        raising=False,
    )

    url = resolve_migration_url(
        explicit_url=(
            "postgresql+asyncpg://experience_test:secret"
            "@experience-test-postgres:5432/pdrd_experience_test"
        ),
    )

    assert "experience-test-postgres" in url
    assert "pdrd_experience_test" in url


def test_structured_settings_use_same_connection_as_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Миграция и приложение используют одну типизированную конфигурацию."""
    monkeypatch.delenv(
        "EXPERIENCE_SERVICE_DATABASE_URL",
        raising=False,
    )

    settings = Settings(
        _env_file=None,
        enabled=True,
        database=DatabaseSettings(
            host="postgres",
            name="pdrd",
            user="experience",
            password="private:@#/password",
        ),
    )

    url = resolve_migration_url(
        settings=settings,
    )

    assert "experience" in url
    assert "private%3A%40%23%2Fpassword" in url
    assert "postgres" in url


def test_disabled_service_cannot_implicitly_migrate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Выключенный сервис не применяет миграцию через runtime settings."""
    monkeypatch.delenv(
        "EXPERIENCE_SERVICE_DATABASE_URL",
        raising=False,
    )

    settings = Settings(
        _env_file=None,
        enabled=False,
    )

    with pytest.raises(
        RuntimeError,
        match="явного включения",
    ):
        resolve_migration_url(
            settings=settings,
        )


def test_conflicting_connection_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Два конкурирующих источника подключения должны приводить к ошибке."""
    monkeypatch.setenv(
        "EXPERIENCE_SERVICE_DATABASE__HOST",
        "another-postgres",
    )

    with pytest.raises(
        ValueError,
        match="одновременно",
    ):
        resolve_migration_url(
            explicit_url=(
                "postgresql+asyncpg://experience_test:secret"
                "@experience-test-postgres:5432/pdrd_experience_test"
            ),
        )


@pytest.mark.parametrize(
    "invalid_url",
    [
        "sqlite:///experience.db",
        "postgresql://postgres/pdrd",
        "postgresql+asyncpg://postgres/pdrd",
        "not-a-url",
    ],
)
def test_invalid_migration_urls_are_rejected(
    invalid_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Alembic не принимает неполные URL и неподдерживаемые драйверы."""
    monkeypatch.delenv(
        "EXPERIENCE_SERVICE_DATABASE__HOST",
        raising=False,
    )

    with pytest.raises(
        ValueError,
    ):
        resolve_migration_url(
            explicit_url=invalid_url,
        )
