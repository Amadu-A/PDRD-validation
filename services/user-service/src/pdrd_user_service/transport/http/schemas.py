# services/user-service/src/pdrd_user_service/transport/http/schemas.py

"""Ограниченные контракты закрытого HTTP API каталога пользователей."""

from datetime import datetime
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from pdrd_user_service.application.use_cases.users import (
    PermissionSnapshot,
    RoleChange,
)
from pdrd_user_service.domain.access import AccessTier, Permission, Role
from pdrd_user_service.domain.identity import UserAccount, UserKind, UserStatus
from pdrd_user_service.domain.role_assignments import RoleScope, ScopeKind


class StrictSchema(BaseModel):
    """Не допускает произвольные поля полномочий от вызывающего клиента."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class UserResponse(StrictSchema):
    """Профиль без пароля, хеша или других секретов аутентификации."""

    user_id: UUID
    kind: UserKind
    tier: AccessTier
    status: UserStatus
    display_name: str
    login: str | None
    email: str | None
    created_at: datetime
    last_login_at: datetime | None
    authorization_version: int

    @classmethod
    def from_domain(cls, user: UserAccount) -> "UserResponse":
        """Копирует только разрешённые для внутреннего API поля."""
        return cls(
            user_id=user.user_id,
            kind=user.kind,
            tier=user.tier,
            status=user.status,
            display_name=user.display_name,
            login=user.login,
            email=user.email,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
            authorization_version=user.authorization_version,
        )


class PermissionResponse(StrictSchema):
    """Операционные права; доступ к объекту проверяется отдельно по области."""

    user_id: UUID
    authorization_version: int
    tier: AccessTier
    status: UserStatus
    roles: tuple[Role, ...]
    permissions: tuple[Permission, ...]
    resource_scope_required: bool = True

    @classmethod
    def from_domain(cls, snapshot: PermissionSnapshot) -> "PermissionResponse":
        """Сериализует один снимок полномочий и его версию."""
        return cls(
            user_id=snapshot.user_id,
            authorization_version=snapshot.authorization_version,
            tier=snapshot.tier,
            status=snapshot.status,
            roles=snapshot.roles,
            permissions=snapshot.permissions,
        )


class ProvisionRequest(StrictSchema):
    """Проверенная auth-service идентичность для идемпотентного создания."""

    provider_id: str = Field(min_length=1, max_length=64)
    namespace: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=512)
    kind: UserKind
    display_name: str = Field(min_length=1, max_length=255)
    login: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)


class ResolveIdentityRequest(StrictSchema):
    """Составной ключ в JSON, не попадающий в URL журнала прокси."""

    provider_id: str = Field(min_length=1, max_length=64)
    namespace: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=512)


class RoleScopeRequest(StrictSchema):
    """Область локального назначения, проверяемая доменной моделью."""

    kind: ScopeKind
    organization_id: UUID | None = None
    department_id: UUID | None = None

    def to_domain(self) -> RoleScope:
        """Применяет инварианты сочетания вида области и UUID."""
        return RoleScope(
            kind=self.kind,
            organization_id=self.organization_id,
            department_id=self.department_id,
        )


class AssignRoleRequest(StrictSchema):
    """Назначение без выбираемого клиентом источника роли и идентификатора."""

    role: Role
    scope: RoleScopeRequest
    expires_at: AwareDatetime | None = None


class RoleChangeResponse(StrictSchema):
    """Новая версия полномочий и сохранённое назначение роли."""

    user_id: UUID
    authorization_version: int
    assignment_id: UUID
    role: Role
    source: str
    scope: RoleScopeRequest
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None

    @classmethod
    def from_domain(cls, result: RoleChange) -> "RoleChangeResponse":
        """Возвращает итог транзакции без каких-либо данных актёра."""
        assignment = result.assignment
        return cls(
            user_id=result.user.user_id,
            authorization_version=result.user.authorization_version,
            assignment_id=assignment.assignment_id,
            role=assignment.role,
            source=assignment.source.value,
            scope=RoleScopeRequest(
                kind=assignment.scope.kind,
                organization_id=assignment.scope.organization_id,
                department_id=assignment.scope.department_id,
            ),
            created_at=assignment.created_at,
            expires_at=assignment.expires_at,
            revoked_at=assignment.revoked_at,
        )
