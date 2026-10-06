# services/admin-service/tests/functional/test_admin_organizations_http.py

"""Проверяет браузерные маршруты организаций и членства без внешней сети."""

from uuid import UUID

from fastapi.testclient import TestClient
from pdrd_admin_service.application.admin_organizations import AdminOrganizations
from pdrd_admin_service.application.admin_users import AdminUsers
from pdrd_admin_service.contracts.models import SessionIdentity
from pdrd_admin_service.contracts.organization_models import (
    DepartmentPage,
    DepartmentResponse,
    MembershipChangeResponse,
    MembershipResponse,
    OrganizationPage,
    OrganizationResponse,
)
from pdrd_admin_service.core.container import ApplicationContainer
from pdrd_admin_service.core.settings import Settings
from pdrd_admin_service.main import create_app

ACTOR = UUID("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa")
USER = UUID("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb")
ORG = UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc")
DEP = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
KEY = "internal-key-longer-than-thirty-two-characters"
CSRF = "csrf-token-which-has-at-least-32-characters"


class Sessions:
    """Даёт прикладному сценарию текущие права проверенной сессии."""

    def __init__(self) -> None:
        """По умолчанию разрешает операции администратора."""
        self.permissions = ("admin.access", "users.roles.assign")

    async def introspect(self, token: str) -> SessionIdentity:
        """Отдаёт доверенный UUID, игнорируя заголовок актёра браузера."""
        assert token == "browser-secret"
        return SessionIdentity(
            user_id=ACTOR, permissions=self.permissions, csrf_token=CSRF
        )


class Directory:
    """Фиксирует команды, которые получит User Service."""

    def __init__(self) -> None:
        """Создаёт пустой журнал вызовов."""
        self.calls: list[tuple[str, object]] = []

    async def list_organizations(
        self, actor_user_id: UUID, *, limit: int, offset: int
    ) -> OrganizationPage:
        """Возвращает ограниченную страницу."""
        self.calls.append(("organizations", (actor_user_id, limit, offset)))
        return OrganizationPage(
            items=(
                OrganizationResponse(organization_id=ORG, name="НеоТерм", active=True),
            ),
            total=1,
            limit=limit,
            offset=offset,
        )

    async def create_organization(
        self, actor_user_id: UUID, *, name: str
    ) -> OrganizationResponse:
        """Возвращает созданную организацию."""
        self.calls.append(("create_organization", (actor_user_id, name)))
        return OrganizationResponse(organization_id=ORG, name=name, active=True)

    async def list_departments(
        self, actor_user_id: UUID, organization_id: UUID, *, limit: int, offset: int
    ) -> DepartmentPage:
        """Возвращает отдел выбранной организации."""
        self.calls.append(("departments", (actor_user_id, organization_id)))
        return DepartmentPage(
            items=(
                DepartmentResponse(
                    department_id=DEP,
                    organization_id=ORG,
                    name="Проектирование",
                    active=True,
                ),
            ),
            total=1,
            limit=limit,
            offset=offset,
        )

    async def create_department(
        self, actor_user_id: UUID, organization_id: UUID, *, name: str
    ) -> DepartmentResponse:
        """Возвращает созданный отдел."""
        self.calls.append(("create_department", (actor_user_id, organization_id, name)))
        return DepartmentResponse(
            department_id=DEP, organization_id=ORG, name=name, active=True
        )

    async def list_memberships(
        self, actor_user_id: UUID, target_user_id: UUID
    ) -> tuple[MembershipResponse, ...]:
        """Возвращает текущее членство."""
        self.calls.append(("memberships", (actor_user_id, target_user_id)))
        return (
            MembershipResponse(
                user_id=USER, organization_id=ORG, department_id=DEP, active=True
            ),
        )

    async def set_membership(
        self,
        actor_user_id: UUID,
        target_user_id: UUID,
        organization_id: UUID,
        department_id: UUID,
        *,
        active: bool,
        authorization_version: int,
    ) -> MembershipChangeResponse:
        """Возвращает новую версию после назначения или снятия."""
        self.calls.append(
            (
                "set_membership",
                (
                    actor_user_id,
                    target_user_id,
                    organization_id,
                    department_id,
                    active,
                    authorization_version,
                ),
            )
        )
        return MembershipChangeResponse(
            user_id=USER,
            authorization_version=authorization_version + 1,
            membership=MembershipResponse(
                user_id=USER, organization_id=ORG, department_id=DEP, active=active
            ),
        )


def make_app() -> tuple[object, Sessions, Directory]:
    """Собирает сервис с фиктивными портами и строгими настройками."""
    sessions, directory = Sessions(), Directory()
    settings = Settings(
        _env_file=None,
        enabled=True,
        auth_service_internal_key=KEY,
        user_service_internal_key=KEY,
    )

    async def ready() -> bool:
        """Подтверждает тестовую готовность."""
        return True

    async def close() -> None:
        """Закрывает пустой контейнер."""

    container = ApplicationContainer(
        settings,
        AdminUsers(sessions, directory),
        ready,
        close,
        AdminOrganizations(sessions, directory),
    )
    return create_app(container), sessions, directory


def test_reads_require_current_admin_and_bound_page() -> None:
    """Список и принадлежность недоступны без cookie или права администратора."""
    app, sessions, directory = make_app()
    with TestClient(app) as client:
        assert client.get("/api/v1/admin/organizations").status_code == 401
        client.cookies.set("pdrd_session", "browser-secret")
        assert client.get("/api/v1/admin/organizations?limit=101").status_code == 422
        page = client.get("/api/v1/admin/organizations?limit=1&offset=0")
        departments = client.get(f"/api/v1/admin/organizations/{ORG}/departments")
        memberships = client.get(f"/api/v1/admin/users/{USER}/memberships")
        sessions.permissions = ()
        denied = client.get("/api/v1/admin/organizations")
    assert page.status_code == 200 and page.json()["items"][0]["name"] == "НеоТерм"
    assert departments.status_code == 200 and departments.json()["total"] == 1
    assert memberships.status_code == 200 and memberships.json()[0]["active"]
    assert denied.status_code == 403
    assert all(call[1][0] == ACTOR for call in directory.calls)


def test_writes_require_csrf_and_use_session_actor() -> None:
    """Браузерный X-PDRD-Actor-Id не заменяет UUID проверенной сессии."""
    app, _, directory = make_app()
    with TestClient(app) as client:
        client.cookies.set("pdrd_session", "browser-secret")
        assert (
            client.post(
                "/api/v1/admin/organizations", json={"name": "НеоТерм"}
            ).status_code
            == 403
        )
        headers = {"X-CSRF-Token": CSRF, "X-PDRD-Actor-Id": str(USER)}
        organization = client.post(
            "/api/v1/admin/organizations", json={"name": "НеоТерм"}, headers=headers
        )
        department = client.post(
            f"/api/v1/admin/organizations/{ORG}/departments",
            json={"name": "Проектирование"},
            headers=headers,
        )
        path = f"/api/v1/admin/users/{USER}/memberships/{ORG}/{DEP}"
        activated = client.put(path, json={"authorization_version": 2}, headers=headers)
        deactivated = client.request(
            "DELETE", path, json={"authorization_version": 3}, headers=headers
        )
    assert organization.status_code == 201
    assert department.status_code == 405
    assert not any(call[0] == "create_department" for call in directory.calls)
    assert (
        activated.status_code == 200 and activated.json()["authorization_version"] == 3
    )
    assert (
        deactivated.status_code == 200
        and not deactivated.json()["membership"]["active"]
    )
    assert all(call[1][0] == ACTOR for call in directory.calls)
