# services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/normative_categories.py

"""Internal HTTP API категорий managed catalog."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
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
    """Возвращает настроенные category use cases."""
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
    """Преобразует category application error в HTTP contract."""
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
    area: CatalogArea = CatalogArea.NORMATIVE,
) -> list[NormativeCategoryResponse]:
    """Возвращает категории указанной области раздела."""
    use_cases = _require_use_cases(
        container,
    )

    try:
        categories = await use_cases.list_categories.execute(
            section_id=section_id,
            area=area,
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
) -> NormativeCategoryResponse:
    """Создаёт категорию внутри выбранной области раздела."""
    use_cases = _require_use_cases(
        container,
    )

    try:
        category = await use_cases.create_category.execute(
            section_id=section_id,
            name=request.name,
            parent_id=request.parent_id,
            area=request.area,
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
) -> NormativeCategoryResponse:
    """Возвращает одну category."""
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
) -> NormativeCategoryResponse:
    """Переименовывает или перемещает category."""
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
) -> DeleteNormativeCategoryResponse:
    """Удаляет category, оставляя документы в разделе."""
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
