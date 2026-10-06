# services/admin-service/src/pdrd_admin_service/infrastructure/clients.py

"""Проверяет ответы внутренних API и скрывает сетевые детали от use case."""

from typing import Any
from uuid import UUID

import httpx
from pydantic import TypeAdapter, ValidationError

from pdrd_admin_service.application.admin_users import (
    AuthenticationRequired,
    InvalidRoleRequest,
    PermissionDenied,
    RoleConflict,
    TargetNotFound,
    UpstreamUnavailable,
)
from pdrd_admin_service.contracts.models import (
    ReplaceRoleRequest,
    RoleDetailResponse,
    SessionIdentity,
    UserPage,
)
from pdrd_admin_service.contracts.normative_access_models import (
    ChangeNormativeAccessRequest,
    NormativeAccessChangeResponse,
)
from pdrd_admin_service.contracts.organization_models import (
    DepartmentPage,
    DepartmentResponse,
    MembershipChangeResponse,
    MembershipResponse,
    OrganizationPage,
    OrganizationResponse,
)
from pdrd_admin_service.contracts.review_access_models import (
    ChangeReviewAccessRequest,
    ReviewAccessChangeResponse,
)
from pdrd_admin_service.contracts.section_models import (
    CatalogSection,
    SectionAccessResponse,
)


def _validated(model: type[Any], response: httpx.Response) -> Any:
    """Считает неверный JSON внутреннего API недоступностью сервиса."""
    try:
        return model.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise UpstreamUnavailable from error


class AuthServiceClient:
    """Передаёт cookie-токен только внутреннему endpoint проверки сессии."""

    def __init__(self, client: httpx.AsyncClient, internal_key: str) -> None:
        """Хранит отдельный служебный ключ для auth-service."""
        self._client = client
        self._internal_key = internal_key

    async def introspect(self, token: str) -> SessionIdentity:
        """Возвращает только проверенный auth-service контекст пользователя."""
        try:
            response = await self._client.post(
                "/internal/v1/auth/introspect",
                headers={"Authorization": f"Bearer {self._internal_key}"},
                json={"token": token},
            )
        except httpx.RequestError as error:
            raise UpstreamUnavailable from error
        if response.status_code == 401:
            raise AuthenticationRequired
        if response.status_code in {403, 404}:
            raise UpstreamUnavailable
        if response.status_code != 200:
            raise UpstreamUnavailable
        return _validated(SessionIdentity, response)


class UserServiceClient:
    """Вызывает только разрешённые маршруты user-service с его ключом."""

    def __init__(self, client: httpx.AsyncClient, internal_key: str) -> None:
        """Не принимает произвольные URL или браузерные заголовки."""
        self._client = client
        self._internal_key = internal_key

    async def _request(
        self,
        method: str,
        path: str,
        *,
        actor_user_id: UUID | None = None,
        params: dict[str, int] | None = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Переводит сетевые сбои и ожидаемые коды в прикладные ошибки."""
        headers = {"Authorization": f"Bearer {self._internal_key}"}
        if actor_user_id is not None:
            headers["X-PDRD-Actor-Id"] = str(actor_user_id)
        try:
            response = await self._client.request(
                method, path, headers=headers, params=params, json=json
            )
        except httpx.RequestError as error:
            raise UpstreamUnavailable from error
        if response.status_code == 404:
            raise TargetNotFound
        if response.status_code == 403:
            raise PermissionDenied
        if response.status_code == 409:
            raise RoleConflict
        if response.status_code == 422:
            raise InvalidRoleRequest
        if response.status_code not in {200, 201}:
            raise UpstreamUnavailable
        return response

    async def list_users(
        self, actor_user_id: UUID, *, limit: int, offset: int
    ) -> UserPage:
        """Читает серверную страницу; браузер не обращается в user-service."""
        response = await self._request(
            "GET",
            "/internal/v1/users",
            actor_user_id=actor_user_id,
            params={"limit": limit, "offset": offset},
        )
        return _validated(UserPage, response)

    async def get_roles(
        self, actor_user_id: UUID, target_user_id: UUID
    ) -> RoleDetailResponse:
        """Читает назначения с дополнительной проверкой актёра в user-service."""
        response = await self._request(
            "GET",
            f"/internal/v1/users/{target_user_id}/roles",
            actor_user_id=actor_user_id,
        )
        return _validated(RoleDetailResponse, response)

    async def get_sections(
        self, actor_user_id: UUID, target_user_id: UUID
    ) -> SectionAccessResponse:
        """Читает живые назначения через защищённый API владельца профилей."""
        response = await self._request(
            "GET",
            f"/internal/v1/users/{target_user_id}/section-access",
            actor_user_id=actor_user_id,
        )
        return _validated(SectionAccessResponse, response)

    async def change_review_access(
        self,
        actor_user_id: UUID,
        target_user_id: UUID,
        command: ChangeReviewAccessRequest,
    ) -> ReviewAccessChangeResponse:
        """Передаёт служебный ключ и актёра из проверенной сессии."""
        response = await self._request(
            "PATCH",
            f"/internal/v1/users/{target_user_id}/review-access",
            actor_user_id=actor_user_id,
            json=command.model_dump(mode="json"),
        )
        return _validated(ReviewAccessChangeResponse, response)

    async def change_normative_access(
        self,
        actor_user_id: UUID,
        target_user_id: UUID,
        command: ChangeNormativeAccessRequest,
    ) -> NormativeAccessChangeResponse:
        """Передаёт служебный ключ и актёра из проверенной сессии."""
        response = await self._request(
            "PATCH",
            f"/internal/v1/users/{target_user_id}/normative-access",
            actor_user_id=actor_user_id,
            json=command.model_dump(mode="json"),
        )
        return _validated(NormativeAccessChangeResponse, response)

    async def replace_role(
        self, actor_user_id: UUID, target_user_id: UUID, command: ReplaceRoleRequest
    ) -> RoleDetailResponse:
        """Просит user-service атомарно заменить локальную рабочую роль."""
        payload = command.model_dump(mode="json")
        if command.section_ids is None:
            payload.pop("section_ids")
        response = await self._request(
            "PATCH",
            f"/internal/v1/users/{target_user_id}/role",
            actor_user_id=actor_user_id,
            json=payload,
        )
        return _validated(RoleDetailResponse, response)

    async def list_organizations(
        self, actor_user_id: UUID, *, limit: int, offset: int
    ) -> OrganizationPage:
        """Читает страницу организаций через проверенный служебный вызов."""
        response = await self._request(
            "GET",
            "/internal/v1/organizations",
            actor_user_id=actor_user_id,
            params={"limit": limit, "offset": offset},
        )
        return _validated(OrganizationPage, response)

    async def create_organization(
        self, actor_user_id: UUID, *, name: str
    ) -> OrganizationResponse:
        """Создаёт организацию без передачи браузерных служебных заголовков."""
        response = await self._request(
            "POST",
            "/internal/v1/organizations",
            actor_user_id=actor_user_id,
            json={"name": name},
        )
        return _validated(OrganizationResponse, response)

    async def list_departments(
        self, actor_user_id: UUID, organization_id: UUID, *, limit: int, offset: int
    ) -> DepartmentPage:
        """Ограничивает отделы выбранной организацией."""
        response = await self._request(
            "GET",
            f"/internal/v1/organizations/{organization_id}/departments",
            actor_user_id=actor_user_id,
            params={"limit": limit, "offset": offset},
        )
        return _validated(DepartmentPage, response)

    async def create_department(
        self, actor_user_id: UUID, organization_id: UUID, *, name: str
    ) -> DepartmentResponse:
        """Создаёт отдел через владельца организационных таблиц."""
        response = await self._request(
            "POST",
            f"/internal/v1/organizations/{organization_id}/departments",
            actor_user_id=actor_user_id,
            json={"name": name},
        )
        return _validated(DepartmentResponse, response)

    async def list_memberships(
        self, actor_user_id: UUID, target_user_id: UUID
    ) -> tuple[MembershipResponse, ...]:
        """Читает членства выбранного пользователя после двойной проверки роли."""
        response = await self._request(
            "GET",
            f"/internal/v1/users/{target_user_id}/memberships",
            actor_user_id=actor_user_id,
        )
        try:
            return TypeAdapter(tuple[MembershipResponse, ...]).validate_python(
                response.json()
            )
        except (ValueError, ValidationError) as error:
            raise UpstreamUnavailable from error

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
        """Назначает или снимает членство атомарно по версии прав."""
        path = (
            f"/internal/v1/users/{target_user_id}/memberships/"
            f"{organization_id}/{department_id}"
        )
        response = await self._request(
            "PUT" if active else "DELETE",
            path,
            actor_user_id=actor_user_id,
            json={"authorization_version": authorization_version},
        )
        return _validated(MembershipChangeResponse, response)


class KnowledgeSectionClient:
    """Читает UUID разделов из исходного каталога без доступа к чужим таблицам."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        """Принимает внутренний HTTP пул из composition root."""
        self._client = client

    async def list_sections(self) -> tuple[CatalogSection, ...]:
        """Валидирует каталог; ошибка не превращается в пустой доступный список."""
        try:
            response = await self._client.get("/internal/v1/normative/sections")
            response.raise_for_status()
            return TypeAdapter(tuple[CatalogSection, ...]).validate_python(
                response.json()
            )
        except (httpx.HTTPError, ValueError, ValidationError) as error:
            raise UpstreamUnavailable from error
