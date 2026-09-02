# services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/normative_sections.py

"""Internal HTTP API разделов нормативной базы."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from pdrd_knowledge_service.application.use_cases.normative_sections import (
    NormativeSectionNotEmptyError,
    NormativeSectionNotFoundError,
    NormativeSectionUpdateError,
    NormativeSectionUseCases,
)
from pdrd_knowledge_service.core.container import (
    ApplicationContainer,
)
from pdrd_knowledge_service.domain.normative_catalog import (
    NormativeCatalogError,
)
from pdrd_knowledge_service.transport.http.dependencies import (
    get_container,
)
from pdrd_knowledge_service.transport.http.schemas.normative_sections import (
    CreateNormativeSectionRequest,
    DeleteNormativeSectionResponse,
    NormativeSectionResponse,
    UpdateNormativeSectionRequest,
)

router = APIRouter(
    prefix="/internal/v1/normative/sections",
    tags=["normative-catalog"],
)

ContainerDependency = Annotated[
    ApplicationContainer,
    Depends(get_container),
]


def _require_use_cases(
    container: ApplicationContainer,
) -> NormativeSectionUseCases:
    """Возвращает настроенные use cases каталога."""
    use_cases = container.normative_sections

    if use_cases is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Normative catalog persistence не настроен.",
        )

    return use_cases


def _translate_error(
    error: Exception,
) -> HTTPException:
    """Преобразует application/domain error в HTTP contract."""
    if isinstance(
        error,
        NormativeSectionNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(
                error,
            ),
        )

    if isinstance(
        error,
        NormativeSectionNotEmptyError,
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
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
    "",
    response_model=list[NormativeSectionResponse],
)
async def list_normative_sections(
    container: ContainerDependency,
) -> list[NormativeSectionResponse]:
    """Возвращает разделы нормативной базы."""
    use_cases = _require_use_cases(
        container,
    )

    sections = await use_cases.list_sections.execute()

    return [
        NormativeSectionResponse.from_domain(
            section,
        )
        for section in sections
    ]


@router.post(
    "",
    response_model=NormativeSectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_normative_section(
    request: CreateNormativeSectionRequest,
    container: ContainerDependency,
) -> NormativeSectionResponse:
    """Создаёт раздел с default system prompt."""
    use_cases = _require_use_cases(
        container,
    )

    try:
        section = await use_cases.create_section.execute(
            name=request.name,
        )

    except NormativeCatalogError as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeSectionResponse.from_domain(
        section,
    )


@router.get(
    "/{section_id}",
    response_model=NormativeSectionResponse,
)
async def get_normative_section(
    section_id: UUID,
    container: ContainerDependency,
) -> NormativeSectionResponse:
    """Возвращает один раздел вместе с сохранённым prompt."""
    use_cases = _require_use_cases(
        container,
    )

    try:
        section = await use_cases.get_section.execute(
            section_id=section_id,
        )

    except NormativeSectionNotFoundError as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeSectionResponse.from_domain(
        section,
    )


@router.patch(
    "/{section_id}",
    response_model=NormativeSectionResponse,
)
async def update_normative_section(
    section_id: UUID,
    request: UpdateNormativeSectionRequest,
    container: ContainerDependency,
) -> NormativeSectionResponse:
    """Переименовывает раздел или сохраняет его system prompt."""
    use_cases = _require_use_cases(
        container,
    )

    try:
        section = await use_cases.update_section.execute(
            section_id=section_id,
            name=request.name,
            system_prompt=request.system_prompt,
        )

    except (
        NormativeCatalogError,
        NormativeSectionNotFoundError,
        NormativeSectionUpdateError,
    ) as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeSectionResponse.from_domain(
        section,
    )


@router.delete(
    "/{section_id}",
    response_model=DeleteNormativeSectionResponse,
)
async def delete_normative_section(
    section_id: UUID,
    container: ContainerDependency,
) -> DeleteNormativeSectionResponse:
    """Удаляет пустой раздел нормативной базы."""
    use_cases = _require_use_cases(
        container,
    )

    try:
        deleted_id = await use_cases.delete_section.execute(
            section_id=section_id,
        )

    except (
        NormativeSectionNotFoundError,
        NormativeSectionNotEmptyError,
    ) as error:
        raise _translate_error(
            error,
        ) from error

    return DeleteNormativeSectionResponse(
        section_id=deleted_id,
    )
