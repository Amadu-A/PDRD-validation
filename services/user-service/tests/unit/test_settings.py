# services/user-service/tests/unit/test_settings.py

"""Проверяет приоритет env и отказ в запуске без секретов User Service."""

from pathlib import Path

import pytest
from pdrd_user_service.core.settings import DatabaseSettings, Settings
from pydantic import ValidationError


def test_disabled_service_accepts_catalog_defaults_without_network() -> None:
    """Выключенный сервис не требует приватного ключа и пароля БД."""
    settings = Settings(_env_file=None)
    assert settings.enabled is False
    assert settings.internal_key.get_secret_value() == ""


def test_enabled_service_requires_key_and_real_database_password() -> None:
    """Прямой запуск Compose не может обойти отсутствие секретов."""
    with pytest.raises(ValidationError, match="USER_SERVICE_INTERNAL_KEY"):
        Settings(
            _env_file=None,
            enabled=True,
            database=DatabaseSettings(password="real-private-password"),
        )
    with pytest.raises(ValidationError, match="Пароль PostgreSQL"):
        Settings(
            _env_file=None,
            enabled=True,
            internal_key="private-key-at-least-thirty-two-characters",
        )
    with pytest.raises(ValidationError, match="не менее 32"):
        Settings(
            _env_file=None,
            enabled=True,
            internal_key="too-short",
            database=DatabaseSettings(password="real-private-password"),
        )


def test_env_example_then_env_then_process_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Приватный env перекрывает каталог, процесс перекрывает оба файла."""
    example = tmp_path / ".env.example"
    private = tmp_path / ".env"
    example.write_text(
        "USER_SERVICE_ENABLED=false\nUSER_SERVICE_DATABASE__HOST=from-example\n",
        encoding="utf-8",
    )
    private.write_text("USER_SERVICE_DATABASE__HOST=from-private\n", encoding="utf-8")
    settings = Settings(_env_file=(example, private))
    assert settings.database.host == "from-private"
    monkeypatch.setenv("USER_SERVICE_DATABASE__HOST", "from-process")
    settings = Settings(_env_file=(example, private))
    assert settings.database.host == "from-process"


def test_secret_values_are_hidden_in_settings_repr() -> None:
    """Случайный вывод настроек не раскрывает служебный ключ и пароль БД."""
    settings = Settings(
        _env_file=None,
        enabled=True,
        internal_key="secret-user-service-key-long-enough",
        database=DatabaseSettings(password="secret-database-password"),
    )
    shown = repr(settings)
    assert "secret-user-service-key-long-enough" not in shown
    assert "secret-database-password" not in shown
