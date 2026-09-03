# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/normative_catalog.py

"""Public HTTP API managed normative catalog."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)

from pdrd_api_gateway.application.ports.normative_catalog_management import (
    NormativeCatalogConflictError,
    NormativeCatalogNotFoundError,
    NormativeCatalogProtocolError,
    NormativeCatalogUnavailableError,
    NormativeCatalogValidationError,
)
from pdrd_api_gateway.application.use_cases.manage_normative_catalog import (
    NormativeCatalogFacade,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.transport.http.dependencies import (
    get_container,
)
from pdrd_api_gateway.transport.http.schemas.normative_catalog import (
    CreateNormativeCategoryRequest,
    CreateNormativeSectionRequest,
    DeleteNormativeCategoryResponse,
    DeleteNormativeDocumentResponse,
    DeleteNormativeSectionResponse,
    MoveNormativeDocumentRequest,
    NormativeCategoryResponse,
    NormativeDocumentResponse,
    NormativeSectionResponse,
    UpdateNormativeCategoryRequest,
    UpdateNormativeSectionRequest,
)

router = APIRouter(
    prefix="/api/v1/normative",
    tags=["normative-catalog"],
)

ContainerDependency = Annotated[
    ApplicationContainer,
    Depends(
        get_container,
    ),
]

_UPLOAD_CHUNK_SIZE = 1024 * 1024


def _require_facade(
    container: ApplicationContainer,
) -> NormativeCatalogFacade:
    """Возвращает configured normative catalog facade."""
    facade = container.normative_catalog

    if facade is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Normative catalog facade не настроен.",
        )

    return facade


def _translate_error(
    error: Exception,
) -> HTTPException:
    """Преобразует application error в public HTTP status."""
    if isinstance(
        error,
        NormativeCatalogNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(
                error,
            ),
        )

    if isinstance(
        error,
        NormativeCatalogConflictError,
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(
                error,
            ),
        )

    if isinstance(
        error,
        NormativeCatalogValidationError,
    ):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(
                error,
            ),
        )

    if isinstance(
        error,
        (
            NormativeCatalogUnavailableError,
            NormativeCatalogProtocolError,
        ),
    ):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(
                error,
            ),
        )

    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Неизвестная ошибка normative catalog.",
    )


async def _read_upload(
    *,
    upload: UploadFile,
    max_upload_bytes: int,
) -> bytes:
    """Читает upload порциями и ограничивает размер."""
    content = bytearray()

    while True:
        chunk = await upload.read(
            _UPLOAD_CHUNK_SIZE,
        )

        if not chunk:
            break

        if (
            len(
                content,
            )
            + len(
                chunk,
            )
            > max_upload_bytes
        ):
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=("Размер нормативного PDF превышает допустимый предел."),
            )

        content.extend(
            chunk,
        )

    return bytes(
        content,
    )


@router.get(
    "/sections",
    response_model=list[NormativeSectionResponse],
)
async def list_normative_sections(
    container: ContainerDependency,
) -> list[NormativeSectionResponse]:
    """Возвращает нормативные разделы."""
    facade = _require_facade(
        container,
    )

    try:
        sections = await facade.list_sections()

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return [
        NormativeSectionResponse.from_view(
            section,
        )
        for section in sections
    ]


@router.post(
    "/sections",
    response_model=NormativeSectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_normative_section(
    request: CreateNormativeSectionRequest,
    container: ContainerDependency,
) -> NormativeSectionResponse:
    """Создаёт нормативный раздел."""
    facade = _require_facade(
        container,
    )

    try:
        section = await facade.create_section(
            name=request.name,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeSectionResponse.from_view(
        section,
    )


@router.get(
    "/sections/{section_id}",
    response_model=NormativeSectionResponse,
)
async def get_normative_section(
    section_id: UUID,
    container: ContainerDependency,
) -> NormativeSectionResponse:
    """Возвращает section с system prompt."""
    facade = _require_facade(
        container,
    )

    try:
        section = await facade.get_section(
            section_id=section_id,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeSectionResponse.from_view(
        section,
    )


@router.patch(
    "/sections/{section_id}",
    response_model=NormativeSectionResponse,
)
async def update_normative_section(
    section_id: UUID,
    request: UpdateNormativeSectionRequest,
    container: ContainerDependency,
) -> NormativeSectionResponse:
    """Переименовывает section или сохраняет system prompt."""
    facade = _require_facade(
        container,
    )

    try:
        section = await facade.update_section(
            section_id=section_id,
            changes=request.model_dump(
                exclude_unset=True,
            ),
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeSectionResponse.from_view(
        section,
    )


@router.delete(
    "/sections/{section_id}",
    response_model=DeleteNormativeSectionResponse,
)
async def delete_normative_section(
    section_id: UUID,
    container: ContainerDependency,
) -> DeleteNormativeSectionResponse:
    """Удаляет пустой section."""
    facade = _require_facade(
        container,
    )

    try:
        deleted_id = await facade.delete_section(
            section_id=section_id,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return DeleteNormativeSectionResponse(
        section_id=deleted_id,
    )


@router.get(
    "/sections/{section_id}/categories",
    response_model=list[NormativeCategoryResponse],
)
async def list_normative_categories(
    section_id: UUID,
    container: ContainerDependency,
) -> list[NormativeCategoryResponse]:
    """Возвращает категории section."""
    facade = _require_facade(
        container,
    )

    try:
        categories = await facade.list_categories(
            section_id=section_id,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return [
        NormativeCategoryResponse.from_view(
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
    """Создаёт category."""
    facade = _require_facade(
        container,
    )

    try:
        category = await facade.create_category(
            section_id=section_id,
            name=request.name,
            parent_id=request.parent_id,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeCategoryResponse.from_view(
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
    """Возвращает category."""
    facade = _require_facade(
        container,
    )

    try:
        category = await facade.get_category(
            category_id=category_id,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeCategoryResponse.from_view(
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
    facade = _require_facade(
        container,
    )

    try:
        category = await facade.update_category(
            category_id=category_id,
            changes=request.model_dump(
                exclude_unset=True,
            ),
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeCategoryResponse.from_view(
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
    """Удаляет category."""
    facade = _require_facade(
        container,
    )

    try:
        deleted_id = await facade.delete_category(
            category_id=category_id,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return DeleteNormativeCategoryResponse(
        category_id=deleted_id,
    )


@router.get(
    "/sections/{section_id}/documents",
    response_model=list[NormativeDocumentResponse],
)
async def list_normative_documents(
    section_id: UUID,
    container: ContainerDependency,
) -> list[NormativeDocumentResponse]:
    """Возвращает документы section."""
    facade = _require_facade(
        container,
    )

    try:
        documents = await facade.list_documents(
            section_id=section_id,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return [
        NormativeDocumentResponse.from_view(
            document,
        )
        for document in documents
    ]


@router.post(
    "/sections/{section_id}/documents",
    response_model=NormativeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_normative_document(
    section_id: UUID,
    file: Annotated[
        UploadFile,
        File(),
    ],
    container: ContainerDependency,
    category_id: Annotated[
        UUID | None,
        Form(),
    ] = None,
) -> NormativeDocumentResponse:
    """Загружает normative PDF через Gateway."""
    facade = _require_facade(
        container,
    )

    try:
        content = await _read_upload(
            upload=file,
            max_upload_bytes=(container.settings.knowledge_service.max_upload_bytes),
        )

        document = await facade.upload_document(
            section_id=section_id,
            category_id=category_id,
            original_name=file.filename or "",
            content=content,
            content_type=(file.content_type or "application/pdf"),
        )

    except HTTPException:
        raise

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    finally:
        await file.close()

    return NormativeDocumentResponse.from_view(
        document,
    )


@router.get(
    "/documents/{document_id}",
    response_model=NormativeDocumentResponse,
)
async def get_normative_document(
    document_id: UUID,
    container: ContainerDependency,
) -> NormativeDocumentResponse:
    """Возвращает document metadata."""
    facade = _require_facade(
        container,
    )

    try:
        document = await facade.get_document(
            document_id=document_id,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeDocumentResponse.from_view(
        document,
    )


@router.patch(
    "/documents/{document_id}",
    response_model=NormativeDocumentResponse,
)
async def move_normative_document(
    document_id: UUID,
    request: MoveNormativeDocumentRequest,
    container: ContainerDependency,
) -> NormativeDocumentResponse:
    """Перемещает document."""
    facade = _require_facade(
        container,
    )

    try:
        document = await facade.move_document(
            document_id=document_id,
            category_id=request.category_id,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeDocumentResponse.from_view(
        document,
    )


@router.delete(
    "/documents/{document_id}",
    response_model=DeleteNormativeDocumentResponse,
)
async def delete_normative_document(
    document_id: UUID,
    container: ContainerDependency,
) -> DeleteNormativeDocumentResponse:
    """Удаляет document по managed lifecycle."""
    facade = _require_facade(
        container,
    )

    try:
        deleted_id = await facade.delete_document(
            document_id=document_id,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return DeleteNormativeDocumentResponse(
        document_id=deleted_id,
    )


@router.post(
    "/documents/{document_id}/index",
    response_model=NormativeDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_normative_document(
    document_id: UUID,
    container: ContainerDependency,
) -> NormativeDocumentResponse:
    """Запускает durable indexing document."""
    facade = _require_facade(
        container,
    )

    try:
        document = await facade.queue_document(
            document_id=document_id,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return NormativeDocumentResponse.from_view(
        document,
    )


@router.get(
    "/documents/{document_id}/content",
)
async def get_normative_document_content(
    document_id: UUID,
    container: ContainerDependency,
) -> Response:
    """Возвращает normative PDF inline."""
    facade = _require_facade(
        container,
    )

    try:
        result = await facade.get_document_content(
            document_id=document_id,
        )

    except Exception as error:
        raise _translate_error(
            error,
        ) from error

    return Response(
        content=result.content,
        media_type=result.mime_type,
        headers={
            "Content-Disposition": (f'inline; filename="{document_id}.pdf"'),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
