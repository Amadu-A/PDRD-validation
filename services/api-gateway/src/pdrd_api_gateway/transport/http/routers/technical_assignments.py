# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/technical_assignments.py

"""Public T content API для citations."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
    status,
)

from pdrd_api_gateway.application.ports.technical_assignment_content import (
    TechnicalAssignmentContentError,
    TechnicalAssignmentContentNotFoundError,
    TechnicalAssignmentContentReader,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.transport.http.dependencies import (
    get_container,
)

router = APIRouter(
    prefix="/api/v1/normative/technical-assignments",
    tags=[
        "technical-assignments",
    ],
)


def _require_reader(
    container: ApplicationContainer,
) -> TechnicalAssignmentContentReader:
    """Возвращает configured T reader."""
    reader = container.technical_assignment_content_reader

    if reader is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Technical assignment content reader не настроен.",
        )

    return reader


@router.get(
    "/{technical_assignment_id}/content",
)
async def get_technical_assignment_content(
    technical_assignment_id: UUID,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> Response:
    """Возвращает T PDF-preview через Gateway."""
    reader = _require_reader(
        container,
    )

    try:
        content = await reader.get_content(
            technical_assignment_id=technical_assignment_id,
        )

    except TechnicalAssignmentContentNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(
                error,
            ),
        ) from error

    except TechnicalAssignmentContentError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(
                error,
            ),
        ) from error

    return Response(
        content=content.content,
        media_type=content.mime_type,
        headers={
            "Content-Disposition": "inline",
        },
    )
