# services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/normative_categories.py

"""Внутреннее HTTP API категорий управляемого каталога."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    status,
)

from pdrd_knowledge_service.application.use_cases.normative_categories import (
    NormativeCategoryNotFoundError,
    NormativeCategoryParentError,
    NormativeCategoryUpdateError,
    NormativeCategoryUseCases,
)
from pdrd_knowledge_service.application.use_cases.normative_sections import (
    NormativeSectionNotFoundError,
)
from pdrd_knowledge_service.application.use_cases.package_access import (
    require_catalog_owner,
)
from pdrd_knowledge_service.core.container import (
    ApplicationContainer,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    CatalogArea,
    NormativeCatalogError,
)
from pdrd_knowledge_service.transport.http.dependencies import (
    get_container,
)
from pdrd_knowledge_service.transport.http.schemas.normative_categories import (
    CreateNormativeCategoryRequest,
    DeleteNormativeCategoryResponse,
    NormativeCategoryResponse,
    UpdateNormativeCategoryRequest,
)

PackageOwner = Annotated[UUID | None, Header(alias="X-PDRD-Package-Owner")]

router = APIRouter(
    prefix="/internal/v1/normative",
    tags=["normative-catalog"],
)

ContainerDependency = Annotated[
    ApplicationContainer,
    Depends(get_container),
]


def _require_use_cases(
    container: ApplicationContainer,
) -> NormativeCategoryUseCases:
    """Возвращает настроенные сценарии работы с категориями."""
    use_cases = container.normative_categories

    if use_cases is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Normative category persistence не настроен.",
        )

    return use_cases


def _translate_error(
    error: Exception,
) -> HTTPException:
    """Преобразует прикладную ошибку категории в HTTP-контракт."""
    if isinstance(
        error,
        (
            NormativeCategoryNotFoundError,
            NormativeSectionNotFoundError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(
                error,
            ),
        )

    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(
            error,
        ),
    )


@router.get(
    "/sections/{section_id}/categories",
    response_model=list[NormativeCategoryResponse],
)
async def list_normative_categories(
    section_id: UUID,
    container: ContainerDependency,
    package_owner: PackageOwner = None,
    area: CatalogArea = CatalogArea.NORMATIVE,
    owner_user_id: UUID | None = None,
) -> list[NormativeCategoryResponse]:
    """Возвращает категории указанной области раздела."""
    if area is CatalogArea.USER_PACKAGE and package_owner is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Требуется владелец личного каталога"
        )
    use_cases = _require_use_cases(
        container,
    )

    try:
        categories = await use_cases.list_categories.execute(
            section_id=section_id,
            area=area,
            owner_user_id=package_owner if area is CatalogArea.USER_PACKAGE else None,
        )

    except NormativeSectionNotFoundError as error:
        raise _translate_error(
            error,
        ) from error

    return [
        NormativeCategoryResponse.from_domain(
            category,
        )
        for category in categories
    ]


@router.post(
    "/sections/{section_id}/categories",
    response_model=NormativeCategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_normative_category(
    section_id: UUID,
    request: CreateNormativeCategoryRequest,
    container: ContainerDependency,
    package_owner: PackageOwner = None,
) -> NormativeCategoryResponse:
    """Создаёт категорию внутри выбранной области раздела."""
    if request.owner_user_id != package_owner or (
        request.area is CatalogArea.USER_PACKAGE and package_owner is None
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Требуется проверенный владелец пакета.",
        )
    use_cases = _require_use_cases(
        container,
    )

    try:
        category = await use_cases.create_category.execute(
            section_id=section_id,
            name=request.name,
            parent_id=request.parent_id,
            area=request.area,
            owner_user_id=request.owner_user_id,
        )

    except (
        NormativeCatalogError,
        NormativeCategoryParentError,
        NormativeSectionNotFoundError,
    ) as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeCategoryResponse.from_domain(
        category,
    )


@router.get(
    "/categories/{category_id}",
    response_model=NormativeCategoryResponse,
)
async def get_normative_category(
    category_id: UUID,
    container: ContainerDependency,
    package_owner: PackageOwner = None,
) -> NormativeCategoryResponse:
    """Возвращает одну категорию."""
    try:
        owned_resource = await _require_use_cases(container).get_category.execute(
            category_id=category_id
        )
        require_catalog_owner(owned_resource, package_owner)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error

    use_cases = _require_use_cases(
        container,
    )

    try:
        category = await use_cases.get_category.execute(
            category_id=category_id,
        )

    except NormativeCategoryNotFoundError as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeCategoryResponse.from_domain(
        category,
    )


@router.patch(
    "/categories/{category_id}",
    response_model=NormativeCategoryResponse,
)
async def update_normative_category(
    category_id: UUID,
    request: UpdateNormativeCategoryRequest,
    container: ContainerDependency,
    package_owner: PackageOwner = None,
) -> NormativeCategoryResponse:
    """Переименовывает или перемещает категорию."""
    try:
        owned_resource = await _require_use_cases(container).get_category.execute(
            category_id=category_id
        )
        require_catalog_owner(owned_resource, package_owner)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error

    use_cases = _require_use_cases(
        container,
    )

    try:
        category = await use_cases.update_category.execute(
            category_id=category_id,
            name=request.name,
            parent_id=request.parent_id,
            change_parent=request.changes_parent,
        )

    except (
        NormativeCatalogError,
        NormativeCategoryNotFoundError,
        NormativeCategoryParentError,
        NormativeCategoryUpdateError,
    ) as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeCategoryResponse.from_domain(
        category,
    )


@router.delete(
    "/categories/{category_id}",
    response_model=DeleteNormativeCategoryResponse,
)
async def delete_normative_category(
    category_id: UUID,
    container: ContainerDependency,
    package_owner: PackageOwner = None,
) -> DeleteNormativeCategoryResponse:
    """Удаляет категорию, оставляя документы в разделе."""
    try:
        owned_resource = await _require_use_cases(container).get_category.execute(
            category_id=category_id
        )
        require_catalog_owner(owned_resource, package_owner)
    except LookupError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error

    use_cases = _require_use_cases(
        container,
    )

    try:
        deleted_id = await use_cases.delete_category.execute(
            category_id=category_id,
        )

    except NormativeCategoryNotFoundError as error:
        raise _translate_error(
            error,
        ) from error

    return DeleteNormativeCategoryResponse(
        category_id=deleted_id,
    )
