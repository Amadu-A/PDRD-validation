# services/auth-service/tests/unit/test_local_accounts.py

"""Проверяет локальный пароль, восстановление bootstrap и маршрутизацию входа."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pdrd_auth_service.application.use_cases.browser_login import (
    BrowserLogin,
    CorporateLoginUnavailable,
)
from pdrd_auth_service.application.use_cases.local_accounts import (
    LocalAccountConflict,
    LocalAccounts,
)
from pdrd_auth_service.domain.local_credential import normalize_username
from pdrd_auth_service.domain.passwords import verify_password

PASSWORD = "test-local-password"


class Store:
    """Подменяет уникальный логин и неизменяемую привязку профиля."""

    def __init__(self):
        """Начинает с пустого хранилища."""
        self.rows = {}

    async def get_by_username(self, username):
        """Возвращает ранее зарезервированную запись."""
        return self.rows.get(username)

    async def create(self, credential):
        """Один логин нельзя создать повторно."""
        if credential.username in self.rows:
            return False
        self.rows[credential.username] = credential
        return True

    async def attach_user(self, subject, user_id):
        """Связывает созданную запись с UUID каталога."""
        name = next(name for name, row in self.rows.items() if row.subject == subject)
        self.rows[name] = replace(self.rows[name], user_id=user_id)


class Profiles:
    """Подменяет каталог, идемпотентный provisioning и возможный сбой связи."""

    def __init__(self):
        """Создаёт постоянный UUID и пустой каталог источников."""
        self.user_id = uuid4()
        self.subjects = []
        self.fail = False
        self.profile = None
        self.provisions = 0

    async def provision_local_superuser(self, *, subject, username):
        """Повтор одного subject возвращает прежний UUID."""
        self.subjects.append(subject)
        if self.fail:
            raise RuntimeError("offline")
        self.profile = SimpleNamespace(
            user_id=self.user_id, kind="local", status="active"
        )
        return self.user_id

    async def find_by_login(self, login):
        """Выбирает источник без получения пароля."""
        return self.profile

    async def provision_corporate(self, identity):
        """Положительный AD ответ создаёт один устойчивый профиль."""
        self.provisions += 1
        self.profile = SimpleNamespace(
            user_id=self.user_id, kind="corporate", status="active"
        )
        return self.user_id


class Sessions:
    """Выдаёт сессию для проверенного внутреннего UUID."""

    async def issue(self, user_id):
        """Позволяет проверить, какому пользователю выдали доступ."""
        return SimpleNamespace(user_id=user_id)


class Corporate:
    """Фиксирует обращение к AD без сохранения пароля."""

    def __init__(self):
        """Начинает без обращений к каталогу AD."""
        self.calls = 0
        self.invalid = False

    async def execute(self, *, login, password):
        """Имитирует положительную проверку либо неверный AD пароль."""
        self.calls += 1
        if self.invalid:
            raise PermissionError("invalid")
        return object()


class External:
    """Фиксирует email-вход для выбора сохранённого источника."""

    def __init__(self, user_id):
        """Использует UUID подтверждённого email-профиля."""
        self.user_id = user_id
        self.calls = 0

    async def authenticate(self, *, login, password):
        """Возвращает UUID только после условной проверки пароля."""
        self.calls += 1
        return self.user_id


@pytest.mark.asyncio
async def test_local_superuser_uses_hash_and_authenticates_without_ad():
    """Пароль хранится только хешем; регистр имени не создаёт другую запись."""
    store, profiles = Store(), Profiles()
    local = LocalAccounts(store, profiles)
    user_id = await local.create_superuser(username=" Admin ", password=PASSWORD)
    assert await local.authenticate(username="ADMIN", password=PASSWORD) == user_id
    credential = store.rows["admin"]
    assert credential.password_hash != PASSWORD
    assert verify_password(PASSWORD, credential.password_hash)
    assert PASSWORD not in repr(credential)
    with pytest.raises(LocalAccountConflict):
        await local.create_superuser(username="admin", password=PASSWORD)


@pytest.mark.asyncio
async def test_interrupted_bootstrap_is_closed_and_recovers_same_subject():
    """При сетевом сбое незавершённая запись не входит и не создаёт новый subject."""
    store, profiles = Store(), Profiles()
    local = LocalAccounts(store, profiles)
    profiles.fail = True
    with pytest.raises(RuntimeError):
        await local.create_superuser(username="admin", password=PASSWORD)
    with pytest.raises(PermissionError):
        await local.authenticate(username="admin", password=PASSWORD)
    with pytest.raises(LocalAccountConflict):
        await local.create_superuser(username="admin", password="different-password")
    profiles.fail = False
    await local.create_superuser(username="admin", password=PASSWORD)
    assert len(set(profiles.subjects)) == 1


@pytest.mark.parametrize("username", ["", "a@b.test", "../admin", "a\n", "x" * 65])
def test_local_username_rejects_unsafe_input(username):
    """Имя не допускает email, путь и управляющие символы."""
    with pytest.raises(ValueError):
        normalize_username(username)


@pytest.mark.asyncio
async def test_local_wrong_password_never_falls_back_to_ad():
    """Совпадение имени с AD не позволяет обойти локальную проверку."""
    store, profiles, corporate = Store(), Profiles(), Corporate()
    local = LocalAccounts(store, profiles)
    await local.create_superuser(username="admin", password=PASSWORD)
    login = BrowserLogin(
        corporate, External(profiles.user_id), profiles, Sessions(), local
    )
    with pytest.raises(PermissionError):
        await login.execute(login="admin", password="wrong-password")
    assert corporate.calls == 0
    assert (
        await login.execute(login="admin", password=PASSWORD)
    ).user_id == profiles.user_id


@pytest.mark.asyncio
async def test_first_and_repeated_ad_login_keep_one_user_and_check_each_password():
    """Профиль создаётся после подтверждения; повторный вход заново проверяет AD."""
    store, profiles, corporate = Store(), Profiles(), Corporate()
    login = BrowserLogin(
        corporate,
        External(profiles.user_id),
        profiles,
        Sessions(),
        LocalAccounts(store, profiles),
    )
    first = await login.execute(login="i.mein", password="AD password")
    second = await login.execute(login="i.mein", password="new AD password")
    assert first.user_id == second.user_id == profiles.user_id
    assert corporate.calls == 2
    assert store.rows == {}
    corporate.invalid = True
    with pytest.raises(PermissionError):
        await login.execute(login="i.mein", password="old AD password")


@pytest.mark.asyncio
async def test_blocked_profile_and_disabled_ad_do_not_issue_session():
    """Локальная блокировка закрывает вход даже при положительном AD."""
    profiles, store, corporate = Profiles(), Store(), Corporate()
    profiles.profile = SimpleNamespace(kind="corporate", status="blocked")
    login = BrowserLogin(
        corporate,
        External(profiles.user_id),
        profiles,
        Sessions(),
        LocalAccounts(store, profiles),
    )
    with pytest.raises(PermissionError):
        await login.execute(login="i.mein", password=PASSWORD)
    assert corporate.calls == 0
    profiles.profile = None
    login = BrowserLogin(
        None,
        External(profiles.user_id),
        profiles,
        Sessions(),
        LocalAccounts(store, profiles),
    )
    with pytest.raises(CorporateLoginUnavailable):
        await login.execute(login="i.mein", password=PASSWORD)


@pytest.mark.asyncio
async def test_saved_email_provider_is_selected_before_ad():
    """Email-аккаунт использует собственный пароль независимо от AD."""
    profiles, store, corporate = Profiles(), Store(), Corporate()
    profiles.profile = SimpleNamespace(
        user_id=profiles.user_id, kind="external", status="active"
    )
    external = External(profiles.user_id)
    login = BrowserLogin(
        corporate, external, profiles, Sessions(), LocalAccounts(store, profiles)
    )
    assert (
        await login.execute(login="client@example.test", password=PASSWORD)
    ).user_id == profiles.user_id
    assert external.calls == 1
    assert corporate.calls == 0
