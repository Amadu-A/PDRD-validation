# services/auth-service/src/pdrd_auth_service/infrastructure/user_service.py

"""Доверенный HTTP-клиент к собственным операциям профиля User Service."""

from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from pdrd_auth_service.application.ports.profiles import (
    PermissionSnapshot,
    ProfileSnapshot,
)
from pdrd_auth_service.application.ports.sessions import UserAccessState
from pdrd_auth_service.domain.identity import CorporateIdentity


class UserServiceUnavailable(RuntimeError):
    """Текущий статус и полномочия нельзя подтвердить через каталог."""


class UserProfile(BaseModel):
    """Открытые поля профиля без секрета аутентификации."""

    model_config = ConfigDict(extra="ignore")

    user_id: UUID
    kind: str
    tier: str
    status: str
    display_name: str
    login: str | None = None
    email: str | None = None
    authorization_version: int = Field(ge=1)


class UserPermissions(BaseModel):
    """Ответ владельца роли с версией для согласования сессии."""

    model_config = ConfigDict(extra="ignore")

    user_id: UUID
    authorization_version: int = Field(ge=1)
    status: str
    tier: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...]


class UserServiceClient:
    """Не открывает прямой доступ к таблицам другого микросервиса."""

    def __init__(
        self,
        base_url: str,
        internal_key: str,
        timeout_seconds: float,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """Принимает подменяемый HTTP-клиент для контрактных тестов."""
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        self._owns_client = client is None
        self._headers = {"Authorization": f"Bearer {internal_key}"}

    async def close(self) -> None:
        """Закрывает только созданный здесь пул HTTP-соединений."""
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self, method: str, path: str, *, json: dict[str, object] | None = None
    ) -> httpx.Response:
        """Отправляет сервисный ключ только во внутреннюю сеть."""
        try:
            response = await self._client.request(
                method, path, headers=self._headers, json=json
            )
        except httpx.HTTPError:
            raise UserServiceUnavailable("Каталог пользователей недоступен") from None
        if response.status_code >= 500:
            raise UserServiceUnavailable("Каталог пользователей недоступен")
        return response

    async def get_user(self, user_id: UUID) -> ProfileSnapshot | None:
        """Получает профиль по внутреннему UUID; 404 означает отсутствие."""
        response = await self._request("GET", f"/internal/v1/users/{user_id}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise UserServiceUnavailable("Каталог пользователей недоступен")
        try:
            profile = UserProfile.model_validate(response.json())
        except (ValueError, TypeError):
            raise UserServiceUnavailable("Некорректный ответ каталога") from None
        return ProfileSnapshot(
            user_id=profile.user_id,
            kind=profile.kind,
            tier=profile.tier,
            status=profile.status,
            display_name=profile.display_name,
            login=profile.login,
            email=profile.email,
            authorization_version=profile.authorization_version,
        )

    async def get_state(self, user_id: UUID) -> UserAccessState | None:
        """Передаёт минимум полей для проверки серверной сессии."""
        profile = await self.get_user(user_id)
        if profile is None:
            return None
        return UserAccessState(
            profile.user_id, profile.status, profile.authorization_version
        )

    async def get_permissions(self, user_id: UUID) -> PermissionSnapshot:
        """Получает роли и права только от владельца каталога."""
        response = await self._request(
            "GET", f"/internal/v1/users/{user_id}/permissions"
        )
        if response.status_code != 200:
            raise UserServiceUnavailable("Полномочия пользователя недоступны")
        try:
            permissions = UserPermissions.model_validate(response.json())
        except (ValueError, TypeError):
            raise UserServiceUnavailable("Некорректный ответ полномочий") from None
        return PermissionSnapshot(
            user_id=permissions.user_id,
            authorization_version=permissions.authorization_version,
            status=permissions.status,
            tier=permissions.tier,
            roles=permissions.roles,
            permissions=permissions.permissions,
        )

    async def provision_corporate(self, identity: CorporateIdentity) -> UUID:
        """Идемпотентно создаёт профиль после успешного LDAPS bind."""
        response = await self._request(
            "POST",
            "/internal/v1/users",
            json={
                "provider_id": identity.provider_id,
                "namespace": identity.namespace,
                "subject": identity.subject,
                "kind": "corporate",
                "display_name": identity.display_name,
                "login": identity.login,
                "email": identity.email,
            },
        )
        if response.status_code != 200:
            raise UserServiceUnavailable("Профиль сотрудника недоступен")
        try:
            return UserProfile.model_validate(response.json()).user_id
        except (ValueError, TypeError):
            raise UserServiceUnavailable("Некорректный ответ каталога") from None

    async def register_external(
        self, *, subject: UUID, display_name: str, email: str
    ) -> UUID:
        """Создаёт только ожидающий профиль без пароля и кода письма."""
        response = await self._request(
            "POST",
            "/internal/v1/users/external",
            json={
                "subject": str(subject),
                "display_name": display_name,
                "email": email,
            },
        )
        if response.status_code != 200:
            raise UserServiceUnavailable("Регистрация профиля недоступна")
        try:
            profile = UserProfile.model_validate(response.json())
        except (ValueError, TypeError):
            raise UserServiceUnavailable("Некорректный ответ каталога") from None
        if profile.kind != "external" or profile.email != email:
            raise UserServiceUnavailable("Профиль не соответствует регистрации")
        return profile.user_id

    async def verify_external(self, *, user_id: UUID, subject: UUID) -> None:
        """Активирует ожидающий профиль после проверки кода в Auth Service."""
        response = await self._request(
            "POST",
            f"/internal/v1/users/{user_id}/verify-email",
            json={"subject": str(subject)},
        )
        if response.status_code != 200:
            raise UserServiceUnavailable("Подтверждение профиля недоступно")
        try:
            profile = UserProfile.model_validate(response.json())
        except (ValueError, TypeError):
            raise UserServiceUnavailable("Некорректный ответ каталога") from None
        if profile.user_id != user_id or profile.status != "active":
            raise UserServiceUnavailable("Профиль не подтверждён")
