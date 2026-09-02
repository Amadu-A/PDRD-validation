# services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/normative_documents.py

"""Internal HTTP read API нормативных документов."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from pdrd_knowledge_service.application.use_cases.normative_documents import (
    NormativeDocumentNotFoundError,
    NormativeDocumentQueryUseCases,
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


def _require_use_cases(
    container: ApplicationContainer,
) -> NormativeDocumentQueryUseCases:
    """Возвращает настроенные document query use cases."""
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
