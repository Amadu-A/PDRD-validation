# services/api-gateway/tests/unit/test_settings.py

"""Unit-тесты типизированной конфигурации API Gateway."""

import pytest
from pdrd_api_gateway.core.settings import Settings
from pydantic import ValidationError


def test_settings_accept_valid_port() -> None:
    """Проверяет допустимый TCP-порт API Gateway."""
    settings = Settings(
        _env_file=None,
        port=8000,
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
        )
