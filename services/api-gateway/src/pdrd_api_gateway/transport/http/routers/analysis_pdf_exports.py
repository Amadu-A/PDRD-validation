# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/analysis_pdf_exports.py

"""HTTP API скачивания annotated PDF analysis artifact."""

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
    status,
)

from pdrd_api_gateway.application.use_cases.get_analysis_annotated_pdf import (
    AnalysisAnnotatedPdfJobNotFoundError,
    AnalysisAnnotatedPdfNotReadyError,
    AnalysisAnnotatedPdfSourceUnavailableError,
    AnalysisAnnotatedPdfUnavailableError,
    GetAnalysisAnnotatedPdf,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.transport.http.dependencies import (
    get_container,
)

router = APIRouter(
    prefix="/api/v1/analyses",
    tags=[
        "analysis-exports",
    ],
)


def require_get_analysis_annotated_pdf(
    container: ApplicationContainer,
) -> GetAnalysisAnnotatedPdf:
    """Возвращает configured annotated PDF use case."""
    if container.get_analysis_annotated_pdf is None:
        raise RuntimeError(
            "GetAnalysisAnnotatedPdf is not configured.",
        )

    return container.get_analysis_annotated_pdf


@router.get(
    "/{job_id}/annotated-pdf",
)
async def get_analysis_annotated_pdf(
    job_id: UUID,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> Response:
    """Скачивает исходный PDF с annotations и report pages."""
    use_case = require_get_analysis_annotated_pdf(
        container,
    )

    try:
        document = await use_case.execute(
            job_id=job_id,
        )

    except AnalysisAnnotatedPdfJobNotFoundError as error:
        raise HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND),
            detail=str(
                error,
            ),
        ) from error

    except AnalysisAnnotatedPdfNotReadyError as error:
        raise HTTPException(
            status_code=(status.HTTP_409_CONFLICT),
            detail=str(
                error,
            ),
        ) from error

    except AnalysisAnnotatedPdfSourceUnavailableError as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(
                error,
            ),
        ) from error

    except AnalysisAnnotatedPdfUnavailableError as error:
        raise HTTPException(
            status_code=(status.HTTP_502_BAD_GATEWAY),
            detail=str(
                error,
            ),
        ) from error

    encoded_file_name = quote(
        document.file_name,
    )

    return Response(
        content=document.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                'attachment; filename="analysis-annotated.pdf"; '
                "filename*=UTF-8''"
                f"{encoded_file_name}"
            ),
        },
    )
