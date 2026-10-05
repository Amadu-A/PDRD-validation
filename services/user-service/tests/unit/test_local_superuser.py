# services/user-service/tests/unit/test_local_superuser.py

"""Проверяет атомарный bootstrap локального профиля без AD и базы данных."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from pdrd_user_service.application.ports.repository import BootstrapAlreadyPerformed
from pdrd_user_service.application.use_cases.local_superuser import LocalSuperusers
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import UserKind, UserStatus


class Users:
    """Хранит профили, устойчивые ключи, роли и singleton guard в памяти."""

    def __init__(self):
        """Создаёт пустой каталог."""
        self.accounts, self.identities, self.assignments = {}, {}, {}
        self.bootstrapped = False

    async def find_identity(self, *key):
        """Ищет по subject без использования отображаемого имени."""
        user_id = self.identities.get(key)
        return self.accounts.get(user_id)

    async def get_user(self, user_id, **_):
        """Возвращает профиль для проверки активности."""
        return self.accounts.get(user_id)

    async def create_user(self, user, identity):
        """Добавляет профиль и устойчивый ключ вместе."""
        self.accounts[user.user_id] = user
        self.identities[identity.stable_key] = user.user_id

    async def list_assignments(self, user_id, **_):
        """Возвращает назначения профиля."""
        return self.assignments.get(user_id, ())

    async def bootstrap_admin(self, user, assignment, version):
        """Запрещает создание второго первого администратора."""
        if self.bootstrapped:
            raise BootstrapAlreadyPerformed
        assert version == 1 and user.authorization_version == 2
        self.bootstrapped = True
        self.accounts[user.user_id] = user
        self.assignments[user.user_id] = (assignment,)


class Work:
    """Откатывает профиль при отказе guard в той же транзакции."""

    def __init__(self, users):
        """Сохраняет транзакционное хранилище."""
        self.users = users
        self.committed = False

    async def __aenter__(self):
        """Запоминает исходное состояние для rollback."""
        self.before = deepcopy(vars(self.users))
        return self

    async def __aexit__(self, *_):
        """Незавершённый bootstrap не оставляет сиротский профиль."""
        if not self.committed:
            vars(self.users).update(self.before)

    async def commit(self):
        """Подтверждает единую транзакцию профиля и роли."""
        self.committed = True


@pytest.mark.asyncio
async def test_local_superuser_bootstrap_is_atomic_and_retry_is_idempotent():
    """Локальный admin активен без AD/email; второй subject откатывается."""
    users, subject = Users(), uuid4()
    service = LocalSuperusers(lambda: Work(users))
    first = await service.create(subject=subject, username="admin")
    again = await service.create(subject=subject, username="admin")
    assert first == again
    assert first.kind is UserKind.LOCAL and first.tier is AccessTier.MEMBER
    assert first.email is None and first.status is UserStatus.ACTIVE
    assert users.assignments[first.user_id][0].role is Role.PLATFORM_ADMIN
    with pytest.raises(BootstrapAlreadyPerformed):
        await service.create(subject=uuid4(), username="second")
    assert len(users.accounts) == len(users.identities) == 1


@pytest.mark.asyncio
async def test_bootstrap_retry_cannot_reactivate_blocked_account():
    """Повтор команды не обходит блокировку в user-service."""
    users, subject = Users(), uuid4()
    service = LocalSuperusers(lambda: Work(users))
    user = await service.create(subject=subject, username="admin")
    users.accounts[user.user_id] = replace(user, status=UserStatus.BLOCKED)
    with pytest.raises(ValueError):
        await service.create(subject=subject, username="admin")
