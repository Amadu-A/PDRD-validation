# services/user-service/tests/functional/test_public_profiles.py

"""Проверяет права, ограничение пакета и HTTP-контракт справочника авторов."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pdrd_user_service.application.use_cases.public_profiles import (
    ProfileReadDenied,
    PublicProfiles,
)
from pdrd_user_service.core.container import ApplicationContainer
from pdrd_user_service.core.settings import DatabaseSettings, Settings
from pdrd_user_service.domain.access import AccessTier, Role
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.domain.role_assignments import (
    RoleAssignment,
    RoleScope,
    RoleSource,
    ScopeKind,
)
from pdrd_user_service.main import create_app

AT = datetime(2026, 10, 6, 10, tzinfo=UTC)
KEY = "public-profile-test-internal-key-at-least-32-characters"


class Profiles:
    """Имитирует собственные таблицы пользователей и назначений ролей."""

    def __init__(self, role=Role.DEPARTMENT_HEAD):
        """Создаёт автора, действующего читателя и профиль с email вместо логина."""
        self.actor = uuid4()
        self.author = uuid4()
        self.accounts = {
            self.actor: UserAccount(
                self.actor,
                UserKind.CORPORATE,
                AccessTier.MEMBER,
                UserStatus.ACTIVE,
                "Пётр Руководитель",
                AT,
                login="p.head",
            ),
            self.author: UserAccount(
                self.author,
                UserKind.EXTERNAL,
                AccessTier.MEMBER,
                UserStatus.ACTIVE,
                "Иван Проектировщик",
                AT,
                email="ivan@example.test",
            ),
        }
        self.assignments = {
            self.actor: (
                RoleAssignment(
                    uuid4(),
                    self.actor,
                    role,
                    RoleSource.LOCAL,
                    RoleScope(
                        ScopeKind.PLATFORM
                        if role is Role.PLATFORM_ADMIN
                        else ScopeKind.SECTIONS
                        if role is Role.DEPARTMENT_HEAD
                        else ScopeKind.OWN
                    ),
                    AT,
                ),
            ),
            self.author: (
                RoleAssignment(
                    uuid4(),
                    self.author,
                    Role.DESIGNER,
                    RoleSource.LOCAL,
                    RoleScope(ScopeKind.OWN),
                    AT,
                ),
            ),
        }

    async def get_user(self, user_id, **options):
        """Возвращает профиль либо отсутствие удалённого автора."""
        return self.accounts.get(user_id)

    async def list_assignments(self, user_id, **options):
        """Возвращает действующие и истёкшие назначения для настоящей доменной проверки."""
        return self.assignments.get(user_id, ())

    async def list_memberships(self, user_id):
        """Новые области ролей не требуют старого членства в отделе."""
        return ()


class Work:
    """Имитирует транзакцию только для чтения профилей."""

    def __init__(self, users):
        """Подключает имитацию репозитория."""
        self.users = users

    async def __aenter__(self):
        """Открывает тестовую транзакцию."""
        return self

    async def __aexit__(self, *errors):
        """Закрывает тестовую транзакцию без внешних ресурсов."""


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.DEPARTMENT_HEAD, Role.PLATFORM_ADMIN])
async def test_batch_returns_only_requested_public_fields_and_current_roles(role):
    """Руководитель и администратор читают авторов; дубли и пропавшие UUID безопасны."""
    users = Profiles(role)
    result = await PublicProfiles(lambda: Work(users), clock=lambda: AT).read(
        actor_user_id=users.actor,
        user_ids=(users.author, users.author, uuid4()),
    )
    assert len(result) == 1
    assert result[0].login == "ivan@example.test"
    assert result[0].display_name == "Иван Проектировщик"
    assert result[0].roles == (Role.DESIGNER,)
    assert not hasattr(result[0], "email")
    assert not hasattr(result[0], "authorization_version")


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["designer", "blocked", "expired"])
async def test_actor_requires_live_catalog_permission(denial):
    """Клиентский UUID не заменяет действующее право читать каталог Experience."""
    users = Profiles(Role.DESIGNER if denial == "designer" else Role.DEPARTMENT_HEAD)
    if denial == "blocked":
        users.accounts[users.actor] = replace(
            users.accounts[users.actor], status=UserStatus.BLOCKED
        )
    if denial == "expired":
        users.assignments[users.actor] = (
            replace(
                users.assignments[users.actor][0],
                created_at=AT - timedelta(hours=2),
                expires_at=AT - timedelta(hours=1),
            ),
        )
    with pytest.raises(ProfileReadDenied):
        await PublicProfiles(lambda: Work(users), clock=lambda: AT).read(
            actor_user_id=users.actor,
            user_ids=(users.author,),
        )


def test_http_service_key_actor_and_maximum_batch_are_enforced():
    """Настоящий закрытый API отвергает секреты/роли в теле и слишком большой пакет."""
    users = Profiles()

    async def close():
        """Закрывает приложение без внешних соединений."""

    container = ApplicationContainer(
        settings=Settings(
            _env_file=None,
            enabled=True,
            internal_key=KEY,
            database=DatabaseSettings(
                _env_file=None, password="public-profile-test-db-password"
            ),
        ),
        readiness=SimpleNamespace(),
        directory=None,
        shutdown_callback=close,
        public_profiles=PublicProfiles(lambda: Work(users), clock=lambda: AT),
    )
    body = {"user_ids": [str(users.author)]}
    headers = {"Authorization": f"Bearer {KEY}", "X-PDRD-Actor-Id": str(users.actor)}
    with TestClient(create_app(container)) as client:
        path = "/internal/v1/users/public-profiles"
        assert client.post(path, json=body).status_code == 401
        assert (
            client.post(
                path, json=body, headers={"Authorization": f"Bearer {KEY}"}
            ).status_code
            == 422
        )
        assert (
            client.post(
                path, json={**body, "actor_user_id": str(users.author)}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(
                path, json={"user_ids": [str(users.author)] * 101}, headers=headers
            ).status_code
            == 422
        )
        response = client.post(path, json=body, headers=headers)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["items"] == [
            {
                "user_id": str(users.author),
                "login": "ivan@example.test",
                "display_name": "Иван Проектировщик",
                "roles": ["designer"],
            }
        ]
