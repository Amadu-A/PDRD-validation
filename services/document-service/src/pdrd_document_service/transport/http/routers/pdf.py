# services/document-service/src/pdrd_document_service/transport/http/routers/pdf.py

"""Internal HTTP API PDF extraction."""

import base64
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)

from pdrd_document_service.application.ports.pdf import (
    PdfProcessingError,
)
from pdrd_document_service.application.use_cases.extract import (
    EmptyPdfError,
    PdfTooLargeError,
)
from pdrd_document_service.core.container import (
    ApplicationContainer,
)
from pdrd_document_service.domain.pdf import (
    InvalidPageSelectionError,
)
from pdrd_document_service.transport.http.dependencies import (
    get_container,
)
from pdrd_document_service.transport.http.schemas.pdf import (
    PdfExtractionResponse,
    PdfPageResponse,
)

router = APIRouter(
    prefix="/internal/v1/pdf",
    tags=["pdf"],
)


@router.post(
    "/extract",
    response_model=PdfExtractionResponse,
)
async def extract_pdf(
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
    file: Annotated[
        UploadFile,
        File(...),
    ],
    pages: Annotated[
        str | None,
        Form(),
    ] = None,
) -> PdfExtractionResponse:
    """Извлекает текст и PNG выбранных PDF-страниц."""
    max_read_size = container.settings.pdf.max_upload_bytes + 1

    try:
        content = await file.read(
            max_read_size,
        )

        try:
            document = container.extract_pdf.execute(
                content=content,
                page_spec=pages,
            )
        except EmptyPdfError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
        except PdfTooLargeError as error:
            raise HTTPException(
                status_code=(status.HTTP_413_CONTENT_TOO_LARGE),
                detail=str(error),
            ) from error
        except InvalidPageSelectionError as error:
            raise HTTPException(
                status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
                detail=str(error),
            ) from error
        except PdfProcessingError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
    finally:
        await file.close()

    return PdfExtractionResponse(
        file_name=(file.filename or "document.pdf"),
        total_pages=document.total_pages,
        selected_pages=list(
            document.selected_page_numbers,
        ),
        pages=[
            PdfPageResponse(
                page_number=page.number,
                page_type=page.page_type,
                text=page.text,
                width_points=page.width_points,
                height_points=page.height_points,
                image_base64=base64.b64encode(
                    page.rendered_png,
                ).decode(
                    "ascii",
                ),
            )
            for page in document.pages
        ],
    )
