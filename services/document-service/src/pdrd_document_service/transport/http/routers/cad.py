# services/document-service/src/pdrd_document_service/transport/http/routers/cad.py

"""Internal HTTP API CAD extraction."""

import base64
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)

from pdrd_document_service.application.ports.cad import (
    CadProcessingError,
    DwgConverterUnavailableError,
)
from pdrd_document_service.application.use_cases.cad import (
    CadTooLargeError,
    EmptyCadError,
)
from pdrd_document_service.core.container import (
    ApplicationContainer,
)
from pdrd_document_service.domain.cad import (
    InvalidCadFilenameError,
)
from pdrd_document_service.transport.http.dependencies import (
    get_container,
)
from pdrd_document_service.transport.http.schemas.cad import (
    CadExtractionResponse,
)

router = APIRouter(
    prefix="/internal/v1/cad",
    tags=["cad"],
)


@router.post(
    "/extract",
    response_model=CadExtractionResponse,
)
async def extract_cad(
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
    file: Annotated[
        UploadFile,
        File(...),
    ],
) -> CadExtractionResponse:
    """Нормализует, анализирует и рендерит DWG/DXF."""
    file_name = file.filename or ""

    max_read_size = container.settings.cad.max_upload_bytes + 1

    try:
        content = await file.read(
            max_read_size,
        )

        try:
            document = container.extract_cad.execute(
                content=content,
                filename=file_name,
            )
        except EmptyCadError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
        except CadTooLargeError as error:
            raise HTTPException(
                status_code=(status.HTTP_413_CONTENT_TOO_LARGE),
                detail=str(error),
            ) from error
        except InvalidCadFilenameError as error:
            raise HTTPException(
                status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
                detail=str(error),
            ) from error
        except DwgConverterUnavailableError as error:
            raise HTTPException(
                status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
                detail=str(error),
            ) from error
        except CadProcessingError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
    finally:
        await file.close()

    return CadExtractionResponse(
        original_file_name=(document.original_file_name),
        original_format=(document.original_format),
        normalized_format=(document.normalized_format),
        converted_from_dwg=(document.converted_from_dwg),
        selected_layout=(document.selected_layout),
        warnings=list(
            document.warnings,
        ),
        machine_data=document.machine_data,
        machine_context=(document.machine_context),
        image_base64=base64.b64encode(
            document.rendered_png,
        ).decode(
            "ascii",
        ),
    )
