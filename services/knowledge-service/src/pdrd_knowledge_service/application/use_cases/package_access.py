# services/knowledge-service/src/pdrd_knowledge_service/application/use_cases/package_access.py

"""Проверяет владельца объектов каталога до изменения и выдачи содержимого."""

from uuid import UUID

from pdrd_knowledge_service.domain.normative_catalog import (
    CatalogArea,
    NormativeCategory,
    NormativeDocument,
)


def require_catalog_owner(
    resource: NormativeCategory | NormativeDocument, owner_user_id: UUID | None
) -> None:
    """Скрывает личные UUID от другого владельца и от общего нормативного API."""
    if resource.area is CatalogArea.USER_PACKAGE and (
        owner_user_id is None or resource.owner_user_id != owner_user_id
    ):
        raise LookupError("Объект каталога не найден.")
    if resource.area is CatalogArea.NORMATIVE and owner_user_id is not None:
        raise LookupError("Объект личного каталога не найден.")
