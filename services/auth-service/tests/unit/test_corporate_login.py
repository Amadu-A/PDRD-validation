# services/auth-service/tests/unit/test_corporate_login.py

"""Юнит-тесты границы корпоративного входа и сохранения пароля."""

from dataclasses import dataclass

import pytest
from pdrd_auth_service.application.ports.credentials import InvalidCorporateCredentials
from pdrd_auth_service.application.use_cases.corporate_login import (
    VerifyCorporateLogin,
)
from pdrd_auth_service.domain.identity import CorporateIdentity


@dataclass
class FakeCredentials:
    """Запоминает только факт вызова для проверки передачи неизменённого пароля."""

    called: bool = False

    async def verify(self, login: str, password: str) -> CorporateIdentity:
        """Проверяет вызов и возвращает тестовую личность без сохранения пароля."""
        self.called = True
        assert login == "i.mein"
        assert password == " пароль с пробелами "
        return CorporateIdentity(
            provider_id="active_directory",
            namespace="itcneoterm.local",
            subject="579a4378-732e-4b46-823f-2b79d49d0a2a",
            login="i.mein",
            display_name="Иван Мейн",
        )


@pytest.mark.asyncio
async def test_short_login_reaches_provider_without_changing_password() -> None:
    """Очищает только логин, оставляя пароль для AD-bind без изменений."""
    provider = FakeCredentials()
    service = VerifyCorporateLogin(provider)
    result = await service.execute(login="  i.mein  ", password=" пароль с пробелами ")

    assert provider.called
    assert result.stable_key[2] == "579a4378-732e-4b46-823f-2b79d49d0a2a"
    assert not hasattr(service, "password")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("login", "password"),
    [
        ("", "secret"),
        (" ", "secret"),
        ("i.mein", ""),
        ("i.mein@elsewhere.local", "secret"),
        ("ITCNEOTERM\\i.mein", "secret"),
        ("i.mein\n", "secret"),
        ("x" * 65, "secret"),
    ],
)
async def test_invalid_input_never_reaches_provider(login: str, password: str) -> None:
    """Пустой пароль не должен превращаться в анонимный LDAP bind."""
    provider = FakeCredentials()
    with pytest.raises(InvalidCorporateCredentials):
        await VerifyCorporateLogin(provider).execute(login=login, password=password)
    assert not provider.called
