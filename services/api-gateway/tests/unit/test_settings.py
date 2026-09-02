# services/api-gateway/tests/unit/test_settings.py

"""Unit-тесты типизированной конфигурации API Gateway."""

import pytest
from pdrd_api_gateway.core.settings import (
    DatabaseSettings,
    Settings,
)
from pydantic import ValidationError


def build_database_settings() -> DatabaseSettings:
    """Создаёт безопасную database-конфигурацию для unit tests."""
    return DatabaseSettings(
        password="unit-test-password",
    )


def test_settings_accept_valid_port() -> None:
    """Проверяет допустимый TCP-порт API Gateway."""
    settings = Settings(
        _env_file=None,
        port=8000,
        database=build_database_settings(),
    )

    assert settings.port == 8000


@pytest.mark.parametrize(
    "invalid_port",
    [
        0,
        65536,
    ],
)
def test_settings_reject_invalid_port(
    invalid_port: int,
) -> None:
    """Проверяет отклонение TCP-порта вне допустимого диапазона."""
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            port=invalid_port,
            database=build_database_settings(),
        )


def test_database_settings_reject_invalid_port() -> None:
    """Проверяет validation PostgreSQL port."""
    with pytest.raises(ValidationError):
        DatabaseSettings(
            port=0,
            password="unit-test-password",
        )


def test_database_password_is_hidden_from_repr() -> None:
    """Проверяет отсутствие database password в repr settings."""
    settings = build_database_settings()

    assert "unit-test-password" not in repr(
        settings,
    )
