# services/admin-service/src/pdrd_admin_service/contracts/organization_models.py

"""Проверяемые контракты административной работы с отделами и членством."""

from uuid import UUID

from pydantic import Field

from pdrd_admin_service.contracts.models import StrictModel


class NameRequest(StrictModel):
    """Ограниченное название новой организации или отдела."""

    name: str = Field(min_length=1, max_length=255)


class MembershipVersionRequest(StrictModel):
    """Версия прав для атомарной смены членства."""

    authorization_version: int = Field(ge=1)


class OrganizationResponse(StrictModel):
    """Организация без служебных полей БД."""

    organization_id: UUID
    name: str
    active: bool


class DepartmentResponse(StrictModel):
    """Отдел внутри единственной организации."""

    department_id: UUID
    organization_id: UUID
    name: str
    active: bool


class OrganizationPage(StrictModel):
    """Ограниченная страница организаций."""

    items: tuple[OrganizationResponse, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class DepartmentPage(StrictModel):
    """Ограниченная страница отделов."""

    items: tuple[DepartmentResponse, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class MembershipResponse(StrictModel):
    """Организационная принадлежность выбранного пользователя."""

    user_id: UUID
    organization_id: UUID
    department_id: UUID | None
    active: bool


class MembershipChangeResponse(StrictModel):
    """Результат смены членства с новой версией полномочий."""

    user_id: UUID
    authorization_version: int = Field(ge=1)
    membership: MembershipResponse
