# services/document-service/src/pdrd_document_service/transport/http/routers/pdf_annotation.py

"""Internal HTTP API построения PDF с annotations."""

import asyncio
from typing import Annotated

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
from pydantic import ValidationError

from pdrd_document_service.application.ports.pdf import (
    PdfProcessingError,
)
from pdrd_document_service.application.use_cases.annotate import (
    EmptyPdfAnnotationSourceError,
    PdfAnnotationSourceTooLargeError,
)
from pdrd_document_service.core.container import (
    ApplicationContainer,
)
from pdrd_document_service.transport.http.dependencies import (
    get_container,
)
from pdrd_document_service.transport.http.schemas.pdf_annotation import (
    PdfAnnotatedDocumentRequest,
)

router = APIRouter(
    prefix="/internal/v1/pdf",
    tags=[
        "pdf",
    ],
)


@router.post(
    "/annotate",
)
async def annotate_pdf(
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
    file: Annotated[
        UploadFile,
        File(
            ...,
        ),
    ],
    payload: Annotated[
        str,
        Form(
            ...,
        ),
    ],
) -> Response:
    """Возвращает исходный PDF с native annotations и appended report."""
    use_case = container.build_annotated_pdf

    if use_case is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Annotated PDF export не настроен."),
        )

    try:
        request = PdfAnnotatedDocumentRequest.model_validate_json(
            payload,
        )

    except ValidationError as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail="Некорректный annotation payload.",
        ) from error

    max_read_size = container.settings.pdf.max_upload_bytes + 1

    try:
        content = await file.read(
            max_read_size,
        )

    finally:
        await file.close()

    try:
        result = await asyncio.to_thread(
            use_case.execute,
            content=content,
            annotations=tuple(
                annotation.to_domain() for annotation in request.annotations
            ),
            report=(request.report.to_domain()),
        )

    except EmptyPdfAnnotationSourceError as error:
        raise HTTPException(
            status_code=(status.HTTP_400_BAD_REQUEST),
            detail=str(
                error,
            ),
        ) from error

    except PdfAnnotationSourceTooLargeError as error:
        raise HTTPException(
            status_code=(status.HTTP_413_CONTENT_TOO_LARGE),
            detail=str(
                error,
            ),
        ) from error

    except PdfProcessingError as error:
        raise HTTPException(
            status_code=(status.HTTP_400_BAD_REQUEST),
            detail=str(
                error,
            ),
        ) from error

    return Response(
        content=result,
        media_type="application/pdf",
    )
