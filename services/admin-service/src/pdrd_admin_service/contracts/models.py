# services/admin-service/src/pdrd_admin_service/contracts/models.py

"""Описывает безопасные HTTP поля пользователей, прав и назначений ролей."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Отклоняет неожиданные поля из соседнего сервиса или браузера."""

    model_config = ConfigDict(extra="forbid")


class SessionIdentity(StrictModel):
    """Результат проверки cookie у auth-service; браузер его не формирует."""

    user_id: UUID
    permissions: tuple[str, ...]
    csrf_token: str = Field(min_length=32)


class UserResponse(StrictModel):
    """Возвращает профиль из user-service без секретов аутентификации."""

    user_id: UUID
    kind: Literal["corporate", "external", "local"]
    tier: Literal["registered_free", "member"]
    status: Literal["pending_verification", "active", "blocked"]
    display_name: str
    login: str | None
    email: str | None
    created_at: AwareDatetime
    last_login_at: AwareDatetime | None
    authorization_version: int = Field(ge=1)


class UserPage(StrictModel):
    """Страница каталога с серверной пагинацией."""

    items: tuple[UserResponse, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class RoleScopeRequest(StrictModel):
    """Сохраняет точную область роли при передаче в user-service."""

    kind: Literal["platform", "organization", "department", "own", "sections"]
    organization_id: UUID | None = None
    department_id: UUID | None = None

    @model_validator(mode="after")
    def require_scope_ids(self) -> "RoleScopeRequest":
        """Проверяет соответствие вида области и обязательных UUID."""
        if self.kind in {"own", "platform", "sections"}:
            valid = self.organization_id is None and self.department_id is None
        elif self.kind == "organization":
            valid = self.organization_id is not None and self.department_id is None
        else:
            valid = self.organization_id is not None and self.department_id is not None
        if not valid:
            raise ValueError("Некорректная область роли")
        return self


class ReplaceRoleRequest(StrictModel):
    """Атомарно заменяет рабочую роль по ожидаемой версии полномочий."""

    section_ids: tuple[UUID, ...] | None = Field(default=None, max_length=1000)
    role: Literal["designer", "department_head", "platform_admin"] | None
    scope: RoleScopeRequest | None
    authorization_version: int = Field(ge=1)

    @model_validator(mode="after")
    def require_role_scope_pair(self) -> "ReplaceRoleRequest":
        """Запрещает область без роли и роль без области."""
        if (self.role is None) != (self.scope is None):
            raise ValueError("Роль и область должны быть заданы вместе")
        return self


class ActiveRole(StrictModel):
    """Текущее назначение роли для карточки администратора."""

    assignment_id: UUID
    role: Literal["designer", "department_head", "platform_admin"]
    source: Literal["local", "ad_group"]
    scope: RoleScopeRequest
    created_at: AwareDatetime
    expires_at: AwareDatetime | None


class RoleDetailResponse(StrictModel):
    """Показывает выбранного пользователя и его действующие назначения."""

    user: UserResponse
    roles: tuple[Literal["designer", "department_head", "platform_admin"], ...]
    assignments: tuple[ActiveRole, ...]
