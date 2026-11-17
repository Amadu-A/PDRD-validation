# services/document-service/src/pdrd_document_service/transport/http/routers/pdf_annotation.py

"""Внутренний HTTP API построения PDF с полным отчётом и аннотациями."""

import asyncio
import logging
import time
import traceback
from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import ValidationError

from pdrd_document_service.application.ports.pdf import (
    PdfProcessingError,
)
from pdrd_document_service.application.ports.pdf_annotation import (
    PdfAnnotationGenerationError,
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
logger = logging.getLogger(__name__)


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
        UploadFile | str,
        File(
            ...,
        ),
    ],
    x_request_id: Annotated[str | None, Header(max_length=100)] = None,
) -> Response:
    """Возвращает исходный PDF с native annotations и appended report."""
    use_case = container.build_annotated_pdf
    started = time.perf_counter()
    request_id = (
        x_request_id
        if x_request_id and all(c.isalnum() or c in "-_" for c in x_request_id)
        else uuid4().hex
    )

    if use_case is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Annotated PDF export не настроен."),
        )

    limit = container.settings.pdf.max_annotation_payload_mb * 1024 * 1024
    if isinstance(payload, str):
        raw_payload = payload.encode("utf-8")
    else:
        try:
            raw_payload = await payload.read(limit + 1)
        finally:
            await payload.close()
    if len(raw_payload) > limit:
        await file.close()
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "annotation_payload_too_large",
                "message": "Превышен допустимый размер отчёта PDF.",
            },
        )

    try:
        request = PdfAnnotatedDocumentRequest.model_validate_json(
            raw_payload,
        )

    except ValidationError as error:
        await file.close()
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

    except PdfAnnotationGenerationError as error:
        cause = error.__cause__ or error
        # Стек сохраняется только в серверном журнале. Сообщение внешней библиотеки
        # может содержать фрагмент PDF, поэтому не включаем его в журнал или ответ.
        logger.error(
            "pdf_annotate operation=%s status=error request_id=%s page=%s annotations=%s "
            "duration_ms=%.2f error_type=%s stack=%s",
            error.operation,
            request_id,
            error.page_number,
            len(request.annotations),
            (time.perf_counter() - started) * 1000,
            type(cause).__name__,
            "\n".join(
                f"{frame.filename}:{frame.lineno} in {frame.name}"
                for frame in traceback.extract_tb(cause.__traceback__)
            ),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "pdf_generation_failed",
                "stage": error.operation,
                "request_id": request_id,
                "message": str(error),
            },
            headers={"X-Request-ID": request_id},
        ) from error

    except PdfProcessingError as error:
        raise HTTPException(
            status_code=(status.HTTP_400_BAD_REQUEST),
            detail={
                "code": "invalid_pdf",
                "request_id": request_id,
                "message": "Исходный PDF или области замечаний некорректны.",
            },
            headers={"X-Request-ID": request_id},
        ) from error

    logger.info(
        "pdf_annotate operation=build status=ok request_id=%s annotations=%s duration_ms=%.2f",
        request_id,
        len(request.annotations),
        (time.perf_counter() - started) * 1000,
    )
    return Response(
        content=result,
        media_type="application/pdf",
        headers={"X-Request-ID": request_id},
    )
