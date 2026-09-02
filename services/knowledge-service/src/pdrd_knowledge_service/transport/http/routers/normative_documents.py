# services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/normative_documents.py

"""Internal HTTP API нормативных документов."""

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

from pdrd_knowledge_service.application.ports.document_storage import (
    NormativeDocumentStorageError,
)
from pdrd_knowledge_service.application.use_cases.normative_documents import (
    NormativeDocumentCategoryError,
    NormativeDocumentNotFoundError,
    NormativeDocumentUploadError,
    NormativeDocumentUseCases,
)
from pdrd_knowledge_service.application.use_cases.normative_sections import (
    NormativeSectionNotFoundError,
)
from pdrd_knowledge_service.core.container import (
    ApplicationContainer,
)
from pdrd_knowledge_service.transport.http.dependencies import (
    get_container,
)
from pdrd_knowledge_service.transport.http.schemas.normative_documents import (
    NormativeDocumentResponse,
)

router = APIRouter(
    prefix="/internal/v1/normative",
    tags=["normative-catalog"],
)

ContainerDependency = Annotated[
    ApplicationContainer,
    Depends(get_container),
]

_UPLOAD_CHUNK_SIZE = 1024 * 1024


def _require_use_cases(
    container: ApplicationContainer,
) -> NormativeDocumentUseCases:
    """Возвращает настроенные document use cases."""
    use_cases = container.normative_documents

    if use_cases is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Normative document persistence не настроен.",
        )

    return use_cases


def _not_found(
    error: Exception,
) -> HTTPException:
    """Преобразует application not-found в HTTP 404."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=str(
            error,
        ),
    )


def _unprocessable(
    error: Exception,
) -> HTTPException:
    """Преобразует validation error в HTTP 422."""
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=str(
            error,
        ),
    )


def _storage_unavailable(
    error: Exception,
) -> HTTPException:
    """Преобразует storage error в HTTP 503."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=str(
            error,
        ),
    )


async def _read_upload_content(
    upload: UploadFile,
    *,
    max_upload_bytes: int,
) -> bytes:
    """Читает multipart upload порциями с ранним size limit."""
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
            raise NormativeDocumentUploadError(
                "Размер нормативного PDF превышает допустимый лимит.",
            )

        content.extend(
            chunk,
        )

    return bytes(
        content,
    )


@router.get(
    "/sections/{section_id}/documents",
    response_model=list[NormativeDocumentResponse],
)
async def list_normative_documents(
    section_id: UUID,
    container: ContainerDependency,
) -> list[NormativeDocumentResponse]:
    """Возвращает документы и состояния индексации."""
    use_cases = _require_use_cases(
        container,
    )

    try:
        documents = await use_cases.list_documents.execute(
            section_id=section_id,
        )

    except NormativeSectionNotFoundError as error:
        raise _not_found(
            error,
        ) from error

    return [
        NormativeDocumentResponse.from_domain(
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
    """Загружает managed PDF в нормативный каталог."""
    use_cases = _require_use_cases(
        container,
    )

    try:
        content = await _read_upload_content(
            file,
            max_upload_bytes=(container.settings.storage.max_upload_bytes),
        )

        document = await use_cases.upload_document.execute(
            section_id=section_id,
            category_id=category_id,
            original_name=file.filename or "",
            content=content,
        )

    except NormativeSectionNotFoundError as error:
        raise _not_found(
            error,
        ) from error

    except (
        NormativeDocumentCategoryError,
        NormativeDocumentUploadError,
    ) as error:
        raise _unprocessable(
            error,
        ) from error

    except NormativeDocumentStorageError as error:
        raise _storage_unavailable(
            error,
        ) from error

    finally:
        await file.close()

    return NormativeDocumentResponse.from_domain(
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
    """Возвращает metadata нормативного документа."""
    use_cases = _require_use_cases(
        container,
    )

    try:
        document = await use_cases.get_document.execute(
            document_id=document_id,
        )

    except NormativeDocumentNotFoundError as error:
        raise _not_found(
            error,
        ) from error

    return NormativeDocumentResponse.from_domain(
        document,
    )


@router.get(
    "/documents/{document_id}/content",
)
async def get_normative_document_content(
    document_id: UUID,
    container: ContainerDependency,
) -> Response:
    """Возвращает PDF inline для просмотра пользователем."""
    use_cases = _require_use_cases(
        container,
    )

    try:
        result = await use_cases.get_document_content.execute(
            document_id=document_id,
        )

    except NormativeDocumentNotFoundError as error:
        raise _not_found(
            error,
        ) from error

    except NormativeDocumentStorageError as error:
        raise _storage_unavailable(
            error,
        ) from error

    return Response(
        content=result.content,
        media_type=result.document.mime_type,
        headers={
            "Content-Disposition": (f'inline; filename="{document_id}.pdf"'),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
