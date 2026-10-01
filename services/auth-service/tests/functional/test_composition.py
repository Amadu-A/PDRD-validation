# services/auth-service/tests/functional/test_composition.py

"""Проверяет сборку сценария входа из изолированной конфигурации auth-service."""

from pathlib import Path

import pytest
from pdrd_auth_service.core import container
from pdrd_auth_service.core.settings import (
    ActiveDirectorySettings,
    DatabaseSettings,
    Settings,
)
from pdrd_auth_service.domain.identity import CorporateIdentity
from pdrd_auth_service.infrastructure.ldaps import LdapsConnectionConfig
from pydantic import SecretStr


def test_disabled_auth_cannot_build_corporate_login() -> None:
    """Без доставленного CA и явного включения сервис не обращается к AD."""
    with pytest.raises(RuntimeError, match="выключен"):
        container.build_corporate_login(Settings(_env_file=None))


@pytest.mark.asyncio
async def test_enabled_auth_passes_verified_settings_to_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Настройки LDAPS переходят в адаптер, пароль в конфигурации не хранится."""
    ca_bundle = tmp_path / "corporate-ca.pem"
    ca_bundle.write_text("offline CA placeholder", encoding="utf-8")
    captured: list[LdapsConnectionConfig] = []

    class FakeVerifier:
        """Заменяет сеть, сохраняя контракт адаптера при проверке композиции."""

        def __init__(self, config: LdapsConnectionConfig) -> None:
            """Фиксирует полученные параметры только для теста."""
            captured.append(config)

        async def verify(self, login: str, password: str) -> CorporateIdentity:
            """Подтверждает передачу пароля только в вызов проверки."""
            assert login == "i.mein"
            assert password == "test-secret"
            return CorporateIdentity(
                provider_id="active_directory",
                namespace="itcneoterm.local",
                subject="579a4378-732e-4b46-823f-2b79d49d0a2a",
                login=login,
                display_name="Иван Мейн",
            )

    monkeypatch.setattr(container, "LdapsCredentialVerifier", FakeVerifier)
    settings = Settings(
        _env_file=None,
        enabled=True,
        ad=ActiveDirectorySettings(
            ca_bundle_path=str(ca_bundle),
            connect_timeout_seconds=3,
            receive_timeout_seconds=7,
        ),
        database=DatabaseSettings(password=SecretStr("test-only")),
    )

    login = container.build_corporate_login(settings)
    identity = await login.execute(login="i.mein", password="test-secret")
    policy = container.build_session_policy(settings)

    assert identity.provider_id == "active_directory"
    assert len(captured) == 1
    assert captured[0].host == "WIN-1L5FI1SGC9J.itcneoterm.local"
    assert captured[0].base_dn == "DC=itcneoterm,DC=local"
    assert captured[0].ca_bundle_path == ca_bundle
    assert captured[0].connect_timeout_seconds == 3
    assert captured[0].receive_timeout_seconds == 7
    assert not hasattr(captured[0], "password")
    assert policy.idle_timeout.total_seconds() == 7200
    assert policy.absolute_timeout.total_seconds() == 28800
