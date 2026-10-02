# services/user-service/src/pdrd_user_service/transport/http/role_replacement_schemas.py

"""Контракты просмотра и атомарной замены рабочей роли сотрудника."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from pdrd_user_service.application.use_cases.replace_role import RoleReplacement
from pdrd_user_service.domain.access import Role
from pdrd_user_service.domain.role_assignments import RoleAssignment
from pdrd_user_service.transport.http.schemas import (
    RoleScopeRequest,
    StrictSchema,
    UserResponse,
)


class ReplaceRoleRequest(StrictSchema):
    """Требует версию прав и выбранную роль с её областью либо оба null."""

    role: Role | None
    scope: RoleScopeRequest | None
    authorization_version: int = Field(ge=1, strict=True)


class ActiveRoleResponse(StrictSchema):
    """Описание одного действующего назначения без секретов источника входа."""

    assignment_id: UUID
    role: Role
    source: str
    scope: RoleScopeRequest
    created_at: datetime
    expires_at: datetime | None

    @classmethod
    def from_domain(cls, assignment: RoleAssignment) -> "ActiveRoleResponse":
        """Сериализует роль и её действующую область."""
        return cls(
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
        )


class RoleReplacementResponse(StrictSchema):
    """Обновлённый профиль, действующие роли и назначения для админки."""

    user: UserResponse
    roles: tuple[Role, ...]
    assignments: tuple[ActiveRoleResponse, ...]

    @classmethod
    def from_domain(cls, result: RoleReplacement) -> "RoleReplacementResponse":
        """Возвращает полный итог замены без паролей и токенов."""
        return cls(
            user=UserResponse.from_domain(result.user),
            roles=result.roles,
            assignments=tuple(
                ActiveRoleResponse.from_domain(item) for item in result.assignments
            ),
        )
