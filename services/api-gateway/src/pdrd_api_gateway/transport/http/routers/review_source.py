# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/review_source.py

"""Закрытый источник Experience, недоступный через nginx /api.

Передаёт исходный PDF и геометрию без копирования изображений листов в Review.
"""

import base64
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from pdrd_api_gateway.application.ports.review import ReviewRequestError
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.transport.http.dependencies import get_container

router = APIRouter(prefix="/internal/v1/review-source", include_in_schema=False)


@router.get("/{job_id}")
async def review_source(
    job_id: UUID,
    request: Request,
    container: Annotated[ApplicationContainer, Depends(get_container)],
) -> dict:
    """Проверяет служебный ключ до загрузки артефактов."""
    settings = container.settings.review
    key = settings.internal_key.get_secret_value()
    if (
        not settings.enabled
        or not key
        or not secrets.compare_digest(
            request.headers.get("authorization", "").encode("utf-8"),
            f"Bearer {key}".encode(),
        )
    ):
        raise HTTPException(403, "Источник доступен только Experience Service.")
    if container.get_review_source is None:
        raise HTTPException(503, "Серверный источник не подключён.")
    try:
        source = await container.get_review_source.execute(job_id=job_id)
    except ReviewRequestError as error:
        raise HTTPException(error.status_code, error.detail) from error
    return {
        "job_id": str(source.job_id),
        "document_id": str(source.document_id),
        "status": "completed",
        "source_filename": source.source_filename,
        "source_sha256": source.source_sha256,
        "pdf_base64": base64.b64encode(source.pdf_content).decode("ascii"),
        "result": source.result,
        "visualization": {
            "job_id": source.visualization.get("job_id"),
            "document_id": source.visualization.get("document_id"),
            "pages": [
                {
                    "page_number": page["page_number"],
                    "locations": page.get("locations", []),
                }
                for page in source.visualization.get("pages", [])
            ],
        },
    }
