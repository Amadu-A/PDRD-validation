# services/api-gateway/src/pdrd_api_gateway/transport/http/section_access.py

"""Сопоставляет объект нормативного API с разделом, назначенным текущему пользователю."""

from uuid import UUID

from fastapi import HTTPException, Request

from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCatalogNotFoundError,
    NormativeCatalogProtocolError,
    NormativeCatalogUnavailableError,
)


async def enforce_normative_section_access(request: Request) -> None:
    """Проверяет область доступа для раздела, категории и документа до изменения или выдачи."""
    authorizer = request.app.state.identity_authorizer
    if authorizer is None or getattr(request.state, "verified_identity", None) is None:
        return
    parts = request.url.path.strip("/").split("/")
    if len(parts) < 5:
        return
    try:
        resource_id = UUID(parts[4])
    except ValueError:
        return
    facade = request.app.state.container.normative_catalog
    if parts[3] == "sections":
        section_id = resource_id
    elif parts[3] in {"categories", "documents"}:
        if facade is None:
            raise HTTPException(503, "Нормативный каталог недоступен")
        try:
            if parts[3] == "categories":
                resource = await facade.get_category(category_id=resource_id)
            else:
                resource = await facade.get_document(document_id=resource_id)
        except NormativeCatalogNotFoundError as error:
            raise HTTPException(404, "Объект не найден") from error
        except (
            NormativeCatalogProtocolError,
            NormativeCatalogUnavailableError,
        ) as error:
            raise HTTPException(503, "Нормативный каталог недоступен") from error
        section_id = resource.section_id
    else:
        return
    denial = await authorizer.require_section(request, section_id)
    if denial is not None:
        raise HTTPException(
            denial.status_code,
            "Нет доступа к разделу"
            if denial.status_code == 403
            else "Проверка раздела недоступна",
        )
