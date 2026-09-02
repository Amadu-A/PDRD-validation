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
from pdrd_document_service.domain.project_context import (
    ExplanatoryNoteContext,
    InvalidExplanatoryNoteRangeError,
)
from pdrd_document_service.transport.http.dependencies import (
    get_container,
)
from pdrd_document_service.transport.http.schemas.pdf import (
    ExplanatoryNoteContextResponse,
    PdfExtractionResponse,
    PdfPageResponse,
    ProjectContextTextPageResponse,
)

router = APIRouter(
    prefix="/internal/v1/pdf",
    tags=["pdf"],
)


def _project_context_response(
    context: ExplanatoryNoteContext,
) -> ExplanatoryNoteContextResponse:
    """Преобразует context domain model в HTTP schema."""
    return ExplanatoryNoteContextResponse(
        enabled=context.enabled,
        start_page=context.start_page,
        end_page=context.end_page,
        pages_count=context.pages_count,
        pages=[
            ProjectContextTextPageResponse(
                page_number=page.number,
                text=page.text,
            )
            for page in context.pages
        ],
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
    use_explanatory_note: Annotated[
        bool,
        Form(),
    ] = False,
    note_start_page: Annotated[
        str | None,
        Form(),
    ] = None,
    note_end_page: Annotated[
        str | None,
        Form(),
    ] = None,
) -> PdfExtractionResponse:
    """Извлекает листы и optional text-only контекст ПЗ."""
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

            if container.extract_pdf_project_context is None:
                if use_explanatory_note:
                    raise HTTPException(
                        status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
                        detail=("Project Context extraction не настроен."),
                    )

                project_context = ExplanatoryNoteContext.disabled()
            else:
                project_context = container.extract_pdf_project_context.execute(
                    content=content,
                    enabled=use_explanatory_note,
                    start_page=note_start_page,
                    end_page=note_end_page,
                )

        except EmptyPdfError as error:
            raise HTTPException(
                status_code=(status.HTTP_400_BAD_REQUEST),
                detail=str(
                    error,
                ),
            ) from error

        except PdfTooLargeError as error:
            raise HTTPException(
                status_code=(status.HTTP_413_CONTENT_TOO_LARGE),
                detail=str(
                    error,
                ),
            ) from error

        except (
            InvalidPageSelectionError,
            InvalidExplanatoryNoteRangeError,
        ) as error:
            raise HTTPException(
                status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
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
                width_points=(page.width_points),
                height_points=(page.height_points),
                image_base64=(
                    base64.b64encode(
                        page.rendered_png,
                    ).decode(
                        "ascii",
                    )
                ),
            )
            for page in document.pages
        ],
        explanatory_note_context=(
            _project_context_response(
                project_context,
            )
        ),
    )
