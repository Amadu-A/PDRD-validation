# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/user_packages.py

"""Public HTTP API пользовательских пакетов документов."""

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
from pdrd_api_gateway.application.use_cases.manage_user_packages import (
    UserPackageCatalogFacade,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.transport.http.dependencies import (
    get_container,
)
from pdrd_api_gateway.transport.http.schemas.normative_catalog import (
    CreateNormativeCategoryRequest,
    DeleteNormativeCategoryResponse,
    DeleteNormativeDocumentResponse,
    MoveNormativeDocumentRequest,
    NormativeCategoryResponse,
    NormativeDocumentResponse,
    UpdateNormativeCategoryRequest,
)

router = APIRouter(
    prefix="/api/v1/normative",
    tags=["user-packages"],
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
) -> UserPackageCatalogFacade:
    """Возвращает configured user-package facade."""
    facade = container.user_package_catalog

    if facade is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User-package catalog facade не настроен.",
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
        detail="Неизвестная ошибка user-package catalog.",
    )


async def _read_upload(
    *,
    upload: UploadFile,
    max_upload_bytes: int,
) -> bytes:
    """Читает package upload порциями и ограничивает размер."""
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
                detail=(
                    "Размер документа пользовательского пакета "
                    "превышает допустимый предел."
                ),
            )

        content.extend(
            chunk,
        )

    return bytes(
        content,
    )


@router.get(
    "/sections/{section_id}/user-packages/categories",
    response_model=list[NormativeCategoryResponse],
)
async def list_user_package_categories(
    section_id: UUID,
    container: ContainerDependency,
) -> list[NormativeCategoryResponse]:
    """Возвращает дерево пользовательских пакетов раздела."""
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
    "/sections/{section_id}/user-packages/categories",
    response_model=NormativeCategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user_package_category(
    section_id: UUID,
    request: CreateNormativeCategoryRequest,
    container: ContainerDependency,
) -> NormativeCategoryResponse:
    """Создаёт пользовательский пакет или вложенную папку."""
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
    "/user-packages/categories/{category_id}",
    response_model=NormativeCategoryResponse,
)
async def get_user_package_category(
    category_id: UUID,
    container: ContainerDependency,
) -> NormativeCategoryResponse:
    """Возвращает package category."""
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
    "/user-packages/categories/{category_id}",
    response_model=NormativeCategoryResponse,
)
async def update_user_package_category(
    category_id: UUID,
    request: UpdateNormativeCategoryRequest,
    container: ContainerDependency,
) -> NormativeCategoryResponse:
    """Переименовывает или перемещает package category."""
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
    "/user-packages/categories/{category_id}",
    response_model=DeleteNormativeCategoryResponse,
)
async def delete_user_package_category(
    category_id: UUID,
    container: ContainerDependency,
) -> DeleteNormativeCategoryResponse:
    """Удаляет package category."""
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
    "/sections/{section_id}/user-packages/documents",
    response_model=list[NormativeDocumentResponse],
)
async def list_user_package_documents(
    section_id: UUID,
    container: ContainerDependency,
) -> list[NormativeDocumentResponse]:
    """Возвращает package documents выбранного раздела."""
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
    "/sections/{section_id}/user-packages/documents",
    response_model=NormativeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_user_package_document(
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
    """Загружает PDF/DOC/DOCX в пользовательский пакет."""
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
            content_type=(file.content_type or "application/octet-stream"),
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
    "/user-packages/documents/{document_id}",
    response_model=NormativeDocumentResponse,
)
async def get_user_package_document(
    document_id: UUID,
    container: ContainerDependency,
) -> NormativeDocumentResponse:
    """Возвращает package document metadata."""
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
    "/user-packages/documents/{document_id}",
    response_model=NormativeDocumentResponse,
)
async def move_user_package_document(
    document_id: UUID,
    request: MoveNormativeDocumentRequest,
    container: ContainerDependency,
) -> NormativeDocumentResponse:
    """Перемещает package document."""
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
    "/user-packages/documents/{document_id}",
    response_model=DeleteNormativeDocumentResponse,
)
async def delete_user_package_document(
    document_id: UUID,
    container: ContainerDependency,
) -> DeleteNormativeDocumentResponse:
    """Удаляет package document."""
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
    "/user-packages/documents/{document_id}/index",
    response_model=NormativeDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_user_package_document(
    document_id: UUID,
    container: ContainerDependency,
) -> NormativeDocumentResponse:
    """Запускает durable indexing package document."""
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
    "/user-packages/documents/{document_id}/content",
)
async def get_user_package_document_content(
    document_id: UUID,
    container: ContainerDependency,
) -> Response:
    """Возвращает package PDF/Word-preview inline."""
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
