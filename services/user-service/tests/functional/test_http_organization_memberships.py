# services/user-service/tests/functional/test_http_organization_memberships.py

"""Проверяет закрытый HTTP контракт организации и членства без PostgreSQL."""

from uuid import UUID

from fastapi.testclient import TestClient
from pdrd_user_service.application.ports.repository import AuthorizationConflict
from pdrd_user_service.application.use_cases.organization_memberships import (
    CatalogPage,
    MembershipChange,
)
from pdrd_user_service.application.use_cases.users import AdminRequired
from pdrd_user_service.core.container import ApplicationContainer
from pdrd_user_service.core.settings import DatabaseSettings, Settings
from pdrd_user_service.domain.identity import Department, Membership, Organization
from pdrd_user_service.main import create_app

KEY = "test-internal-key-which-remains-private"
ACTOR = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
USER = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")
ORG = UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc")
DEP = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")


class Ready:
    """Подтверждает готовность тестового контейнера."""

    async def is_ready(self) -> bool:
        """Не подключается к БД."""
        return True


class Operations:
    """Фиксирует защищённые вызовы прикладного сценария."""

    def __init__(self) -> None:
        """Создаёт пустой журнал и флаги отказов."""
        self.calls: list[tuple[str, object]] = []
        self.deny = False
        self.conflict = False

    def _check(self, actor_user_id: UUID) -> None:
        """Имитирует повторную проверку роли в БД."""
        assert actor_user_id == ACTOR
        if self.deny:
            raise AdminRequired

    async def organizations(self, **values: object) -> CatalogPage:
        """Возвращает страницу с проверенным актёром."""
        self._check(values["actor_user_id"])
        self.calls.append(("organizations", values))
        return CatalogPage(
            (Organization(ORG, "НеоТерм"),), 1, values["limit"], values["offset"]
        )

    async def create_organization(self, **values: object) -> Organization:
        """Создаёт запись справочника."""
        self._check(values["actor_user_id"])
        self.calls.append(("create_organization", values))
        return Organization(ORG, values["name"])

    async def departments(self, **values: object) -> CatalogPage:
        """Возвращает отдел только выбранной организации."""
        self._check(values["actor_user_id"])
        assert values["organization_id"] == ORG
        self.calls.append(("departments", values))
        return CatalogPage(
            (Department(DEP, ORG, "Проектирование"),),
            1,
            values["limit"],
            values["offset"],
        )

    async def create_department(self, **values: object) -> Department:
        """Фиксирует создание внутри указанной организации."""
        self._check(values["actor_user_id"])
        self.calls.append(("create_department", values))
        return Department(DEP, values["organization_id"], values["name"])

    async def memberships(self, **values: object) -> tuple[Membership, ...]:
        """Возвращает членство выбранного пользователя."""
        self._check(values["actor_user_id"])
        self.calls.append(("memberships", values))
        return (Membership(USER, ORG, DEP),)

    async def set_membership(self, **values: object) -> MembershipChange:
        """Фиксирует CAS и моделирует конфликт версии."""
        self._check(values["actor_user_id"])
        self.calls.append(("set_membership", values))
        if self.conflict:
            raise AuthorizationConflict
        return MembershipChange(
            USER,
            values["authorization_version"] + 1,
            Membership(USER, ORG, DEP, values["active"]),
        )


def client_and_operations() -> tuple[TestClient, Operations]:
    """Собирает HTTP приложение с подменённым сценарием."""
    operations = Operations()
    settings = Settings(
        _env_file=None,
        enabled=True,
        internal_key=KEY,
        database=DatabaseSettings(password="private-test-password"),
    )

    async def close() -> None:
        """Закрывает пустой контейнер."""

    container = ApplicationContainer(
        settings=settings,
        readiness=Ready(),
        directory=None,
        shutdown_callback=close,
        organization_memberships=operations,
    )
    return TestClient(create_app(container)), operations


def headers() -> dict[str, str]:
    """Передаёт только разрешённые служебные заголовки."""
    return {"Authorization": f"Bearer {KEY}", "X-PDRD-Actor-Id": str(ACTOR)}


def test_catalog_routes_require_key_actor_and_bounded_pages() -> None:
    """Браузер не читает каталог напрямую, а администратор получает страницу."""
    client, operations = client_and_operations()
    with client:
        assert client.get("/internal/v1/organizations").status_code == 401
        assert (
            client.get(
                "/internal/v1/organizations",
                headers={"Authorization": f"Bearer {KEY}"},
            ).status_code
            == 422
        )
        assert (
            client.get(
                "/internal/v1/organizations?limit=101", headers=headers()
            ).status_code
            == 422
        )
        page = client.get(
            "/internal/v1/organizations?limit=1&offset=2", headers=headers()
        )
        created = client.post(
            "/internal/v1/organizations", headers=headers(), json={"name": "НеоТерм"}
        )
        departments = client.get(
            f"/internal/v1/organizations/{ORG}/departments?limit=1", headers=headers()
        )
        department = client.post(
            f"/internal/v1/organizations/{ORG}/departments",
            headers=headers(),
            json={"name": "Проектирование"},
        )
    assert page.status_code == 200 and page.json()["total"] == 1
    assert created.status_code == 201 and created.json()["organization_id"] == str(ORG)
    assert departments.status_code == 200 and departments.json()["items"][0][
        "department_id"
    ] == str(DEP)
    assert department.status_code == 201
    assert len(operations.calls) == 4


def test_membership_routes_forward_version_and_fail_closed() -> None:
    """Назначение и отзыв требуют служебный ключ и версию полномочий."""
    client, operations = client_and_operations()
    path = f"/internal/v1/users/{USER}/memberships/{ORG}/{DEP}"
    with client:
        assert client.put(path, json={"authorization_version": 2}).status_code == 401
        assert client.put(path, headers=headers(), json={}).status_code == 422
        listed = client.get(f"/internal/v1/users/{USER}/memberships", headers=headers())
        activated = client.put(
            path, headers=headers(), json={"authorization_version": 2}
        )
        operations.conflict = True
        conflict = client.request(
            "DELETE", path, headers=headers(), json={"authorization_version": 3}
        )
        operations.conflict = False
        operations.deny = True
        denied = client.put(path, headers=headers(), json={"authorization_version": 3})
    assert listed.status_code == 200 and listed.json()[0]["active"]
    assert activated.status_code == 200
    assert activated.json()["authorization_version"] == 3
    assert conflict.status_code == 409
    assert denied.status_code == 403
    assert operations.calls[1][1]["authorization_version"] == 2
    assert operations.calls[2][1]["active"] is False
