# services/document-service/src/pdrd_document_service/transport/http/routers/combined.py

"""Internal HTTP API объединённой PDF + CAD подготовки."""

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

from pdrd_document_service.application.ports.cad import (
    CadProcessingError,
    DwgConverterUnavailableError,
)
from pdrd_document_service.application.ports.image_composer import (
    ImageCompositionError,
)
from pdrd_document_service.application.ports.pdf import (
    PdfProcessingError,
)
from pdrd_document_service.application.use_cases.cad import (
    CadTooLargeError,
    EmptyCadError,
)
from pdrd_document_service.application.use_cases.extract import (
    EmptyPdfError,
    PdfTooLargeError,
)
from pdrd_document_service.core.container import (
    ApplicationContainer,
)
from pdrd_document_service.domain.cad import (
    InvalidCadFilenameError,
)
from pdrd_document_service.domain.combined import (
    CombinedPageSelectionError,
)
from pdrd_document_service.domain.pdf import (
    InvalidPageSelectionError,
)
from pdrd_document_service.transport.http.dependencies import (
    get_container,
)
from pdrd_document_service.transport.http.schemas.cad import (
    CadExtractionResponse,
)
from pdrd_document_service.transport.http.schemas.combined import (
    CombinedExtractionResponse,
)
from pdrd_document_service.transport.http.schemas.pdf import (
    PdfPageResponse,
)

router = APIRouter(
    prefix="/internal/v1/combined",
    tags=["combined"],
)


@router.post(
    "/extract",
    response_model=CombinedExtractionResponse,
)
async def extract_combined(
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
    pdf: Annotated[
        UploadFile,
        File(...),
    ],
    cad: Annotated[
        UploadFile,
        File(...),
    ],
    pages: Annotated[
        str | None,
        Form(),
    ] = None,
) -> CombinedExtractionResponse:
    """Подготавливает соответствующие PDF и CAD представления."""
    pdf_file_name = pdf.filename or "document.pdf"

    cad_file_name = cad.filename or ""

    try:
        pdf_content = await pdf.read(
            (container.settings.pdf.max_upload_bytes + 1),
        )

        cad_content = await cad.read(
            (container.settings.cad.max_upload_bytes + 1),
        )

        try:
            document = container.extract_combined.execute(
                pdf_content=pdf_content,
                cad_content=cad_content,
                cad_filename=cad_file_name,
                page_spec=pages,
            )

        except EmptyPdfError as error:
            raise HTTPException(
                status_code=(status.HTTP_400_BAD_REQUEST),
                detail=str(
                    error,
                ),
            ) from error

        except EmptyCadError as error:
            raise HTTPException(
                status_code=(status.HTTP_400_BAD_REQUEST),
                detail=str(
                    error,
                ),
            ) from error

        except (
            PdfTooLargeError,
            CadTooLargeError,
        ) as error:
            raise HTTPException(
                status_code=(status.HTTP_413_CONTENT_TOO_LARGE),
                detail=str(
                    error,
                ),
            ) from error

        except (
            InvalidPageSelectionError,
            CombinedPageSelectionError,
            InvalidCadFilenameError,
        ) as error:
            raise HTTPException(
                status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
                detail=str(
                    error,
                ),
            ) from error

        except DwgConverterUnavailableError as error:
            raise HTTPException(
                status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
                detail=str(
                    error,
                ),
            ) from error

        except (
            PdfProcessingError,
            CadProcessingError,
        ) as error:
            raise HTTPException(
                status_code=(status.HTTP_400_BAD_REQUEST),
                detail=str(
                    error,
                ),
            ) from error

        except ImageCompositionError as error:
            raise HTTPException(
                status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
                detail=str(
                    error,
                ),
            ) from error

    finally:
        await pdf.close()
        await cad.close()

    page = document.page
    cad_document = document.cad

    return CombinedExtractionResponse(
        pdf_file_name=pdf_file_name,
        cad_file_name=cad_file_name,
        total_pdf_pages=(document.pdf.total_pages),
        selected_page=page.number,
        analysis_text=(document.analysis_text),
        pdf=PdfPageResponse(
            page_number=page.number,
            page_type=page.page_type,
            text=page.text,
            width_points=(page.width_points),
            height_points=(page.height_points),
            image_base64=base64.b64encode(
                page.rendered_png,
            ).decode(
                "ascii",
            ),
        ),
        cad=CadExtractionResponse(
            original_file_name=(cad_document.original_file_name),
            original_format=(cad_document.original_format),
            normalized_format=(cad_document.normalized_format),
            converted_from_dwg=(cad_document.converted_from_dwg),
            selected_layout=(cad_document.selected_layout),
            warnings=list(
                cad_document.warnings,
            ),
            machine_data=(cad_document.machine_data),
            machine_context=(cad_document.machine_context),
            image_base64=base64.b64encode(
                cad_document.rendered_png,
            ).decode(
                "ascii",
            ),
        ),
        combined_image_base64=(
            base64.b64encode(
                document.combined_rendered_png,
            ).decode(
                "ascii",
            )
        ),
    )
