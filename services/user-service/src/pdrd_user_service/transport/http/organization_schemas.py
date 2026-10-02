# services/user-service/src/pdrd_user_service/transport/http/organization_schemas.py

"""Контракты внутреннего API организаций, отделов и членства."""

from uuid import UUID

from pydantic import Field

from pdrd_user_service.application.use_cases.organization_memberships import (
    CatalogPage,
    MembershipChange,
)
from pdrd_user_service.domain.identity import Department, Membership, Organization
from pdrd_user_service.transport.http.schemas import StrictSchema


class NameRequest(StrictSchema):
    """Название нового объекта справочника без произвольных полей."""

    name: str = Field(min_length=1, max_length=255)


class MembershipVersionRequest(StrictSchema):
    """Ожидаемая версия прав при назначении или снятии членства."""

    authorization_version: int = Field(ge=1)


class OrganizationResponse(StrictSchema):
    """Безопасное представление организации."""

    organization_id: UUID
    name: str
    active: bool

    @classmethod
    def from_domain(cls, value: Organization) -> "OrganizationResponse":
        """Копирует только поля справочника."""
        return cls(
            organization_id=value.organization_id, name=value.name, active=value.active
        )


class DepartmentResponse(StrictSchema):
    """Безопасное представление отдела внутри организации."""

    department_id: UUID
    organization_id: UUID
    name: str
    active: bool

    @classmethod
    def from_domain(cls, value: Department) -> "DepartmentResponse":
        """Копирует только поля справочника."""
        return cls(
            department_id=value.department_id,
            organization_id=value.organization_id,
            name=value.name,
            active=value.active,
        )


class OrganizationPageResponse(StrictSchema):
    """Страница организаций с общим количеством."""

    items: tuple[OrganizationResponse, ...]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_domain(cls, page: CatalogPage) -> "OrganizationPageResponse":
        """Сериализует ограниченную страницу организаций."""
        if any(not isinstance(item, Organization) for item in page.items):
            raise TypeError("Страница содержит не организации")
        return cls(
            items=tuple(OrganizationResponse.from_domain(item) for item in page.items),
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )


class DepartmentPageResponse(StrictSchema):
    """Страница отделов одной организации."""

    items: tuple[DepartmentResponse, ...]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_domain(cls, page: CatalogPage) -> "DepartmentPageResponse":
        """Сериализует ограниченную страницу отделов."""
        if any(not isinstance(item, Department) for item in page.items):
            raise TypeError("Страница содержит не отделы")
        return cls(
            items=tuple(DepartmentResponse.from_domain(item) for item in page.items),
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )


class MembershipResponse(StrictSchema):
    """Членство без сведений о паролях и токенах."""

    user_id: UUID
    organization_id: UUID
    department_id: UUID | None
    active: bool

    @classmethod
    def from_domain(cls, value: Membership) -> "MembershipResponse":
        """Копирует область и действующее состояние."""
        return cls(
            user_id=value.user_id,
            organization_id=value.organization_id,
            department_id=value.department_id,
            active=value.active,
        )


class MembershipChangeResponse(StrictSchema):
    """Состояние после CAS перехода членства."""

    user_id: UUID
    authorization_version: int
    membership: MembershipResponse

    @classmethod
    def from_domain(cls, value: MembershipChange) -> "MembershipChangeResponse":
        """Возвращает новую версию для следующего изменения роли."""
        return cls(
            user_id=value.user_id,
            authorization_version=value.authorization_version,
            membership=MembershipResponse.from_domain(value.membership),
        )
