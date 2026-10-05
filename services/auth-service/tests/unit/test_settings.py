# services/auth-service/tests/unit/test_settings.py

"""Проверяет приоритет конфигурации и запрет неподтверждённого LDAPS."""

from pathlib import Path

import pytest
from pdrd_auth_service.core.settings import (
    ActiveDirectorySettings,
    DatabaseSettings,
    SessionSettings,
    Settings,
)
from pydantic import SecretStr, ValidationError


def test_disabled_auth_does_not_need_certificate() -> None:
    """До получения корпоративного CA пакет импортируется без доступа к AD."""
    settings = Settings(_env_file=None)

    assert settings.enabled is False
    assert settings.ad.port == 636
    assert settings.ad.ca_bundle_path == ""


def test_enabled_auth_requires_readable_ca_bundle(tmp_path: Path) -> None:
    """Включение LDAPS без файла доверенного CA завершается ошибкой."""
    with pytest.raises(ValidationError, match="CA_BUNDLE_PATH не задан"):
        Settings(_env_file=None, enabled=True)

    with pytest.raises(ValidationError, match="должен быть читаемым файлом"):
        Settings(
            _env_file=None,
            enabled=True,
            ad=ActiveDirectorySettings(ca_bundle_path=str(tmp_path / "missing.pem")),
        )


@pytest.mark.parametrize("hostname", ["192.168.10.5", "localhost", "dc.invalid name"])
def test_enabled_auth_rejects_ip_and_invalid_dns_name(
    hostname: str, tmp_path: Path
) -> None:
    """Имя контроллера должно подходить для проверки сертификата TLS."""
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("test certificate placeholder", encoding="utf-8")

    with pytest.raises(ValidationError, match="CONTROLLER_HOST"):
        Settings(
            _env_file=None,
            enabled=True,
            ad=ActiveDirectorySettings(
                controller_host=hostname, ca_bundle_path=str(ca_bundle)
            ),
        )


def test_valid_configuration_keeps_confirmed_ad_values(tmp_path: Path) -> None:
    """Разрешённый запуск сохраняет FQDN, домен и базовый DN каталога."""
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("test certificate placeholder", encoding="utf-8")

    settings = Settings(
        _env_file=None,
        enabled=True,
        ad=ActiveDirectorySettings(ca_bundle_path=str(ca_bundle)),
        database=DatabaseSettings(password=SecretStr("test-only")),
    )

    assert settings.ad.controller_host == "WIN-1L5FI1SGC9J.itcneoterm.local"
    assert settings.ad.domain == "itcneoterm.local"
    assert settings.ad.base_dn == "DC=itcneoterm,DC=local"


def test_ad_receive_timeout_is_integer() -> None:
    """ldap3 получает целое число секунд для POSIX SO_RCVTIMEO."""
    settings = ActiveDirectorySettings()

    assert settings.receive_timeout_seconds == 10
    assert isinstance(settings.receive_timeout_seconds, int)

    with pytest.raises(ValidationError):
        ActiveDirectorySettings(receive_timeout_seconds=10.5)


def test_enabled_auth_requires_database_password(tmp_path: Path) -> None:
    """Рабочий процесс не выдаёт сессию без собственного хранилища."""
    ca_bundle = tmp_path / "ca.pem"
    ca_bundle.write_text("offline fixture", encoding="utf-8")
    with pytest.raises(ValidationError, match="Пароль PostgreSQL"):
        Settings(
            _env_file=None,
            enabled=True,
            ad=ActiveDirectorySettings(ca_bundle_path=str(ca_bundle)),
        )


def test_private_env_and_process_override_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Закрытый env и переменные процесса переопределяют каталог последовательно."""
    example = tmp_path / ".env.example"
    private = tmp_path / ".env"
    example.write_text(
        "AUTH_SERVICE_ENABLED=false\nAUTH_SERVICE_AD__PORT=636\n", encoding="utf-8"
    )
    private.write_text("AUTH_SERVICE_AD__PORT=1636\n", encoding="utf-8")

    settings = Settings(_env_file=(example, private))
    assert settings.ad.port == 1636

    monkeypatch.setenv("AUTH_SERVICE_AD__PORT", "2636")
    settings = Settings(_env_file=(example, private))
    assert settings.ad.port == 2636


def test_session_timeouts_are_bounded_and_consistent() -> None:
    """Настройка не допускает простоя длиннее абсолютного срока."""
    assert SessionSettings().idle_timeout_seconds == 7200
    assert SessionSettings().absolute_timeout_seconds == 28800
    with pytest.raises(ValidationError, match="Срок простоя"):
        SessionSettings(idle_timeout_seconds=9000, absolute_timeout_seconds=3600)
    with pytest.raises(ValidationError):
        SessionSettings(absolute_timeout_seconds=86401)
