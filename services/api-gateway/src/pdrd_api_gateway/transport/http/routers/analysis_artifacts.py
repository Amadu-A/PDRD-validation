# services/api-gateway/src/pdrd_api_gateway/transport/http/routers/analysis_artifacts.py

"""Internal HTTP API reusable analysis artifacts."""

from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from pdrd_api_gateway.application.ports.artifacts import (
    AnalysisArtifactsNotFoundError,
    AnalysisArtifactStorageError,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.transport.http.dependencies import (
    get_container,
)
from pdrd_api_gateway.transport.http.schemas.analysis_artifacts import (
    SaveAnalysisVisualizationArtifactRequest,
    SaveAnalysisVisualizationArtifactResponse,
)

router = APIRouter(
    prefix="/internal/v1/analysis-artifacts",
    tags=[
        "analysis-artifacts",
    ],
)


@router.post(
    "/{document_id}/visualization",
    response_model=(SaveAnalysisVisualizationArtifactResponse),
)
async def save_analysis_visualization_artifact(
    document_id: UUID,
    request: SaveAnalysisVisualizationArtifactRequest,
    container: Annotated[
        ApplicationContainer,
        Depends(
            get_container,
        ),
    ],
) -> SaveAnalysisVisualizationArtifactResponse:
    """Сохраняет результат initial PDF extraction для lazy visualization."""
    artifact_store = container.artifact_store

    if artifact_store is None:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail=("Analysis artifact storage не настроен."),
        )

    try:
        pages = tuple(page.to_domain() for page in request.pages)

        await artifact_store.save_visualization(
            document_id=document_id,
            pages=pages,
        )

    except ValueError as error:
        raise HTTPException(
            status_code=(status.HTTP_422_UNPROCESSABLE_CONTENT),
            detail=str(
                error,
            ),
        ) from error

    except AnalysisArtifactsNotFoundError as error:
        raise HTTPException(
            status_code=(status.HTTP_404_NOT_FOUND),
            detail=str(
                error,
            ),
        ) from error

    except AnalysisArtifactStorageError as error:
        raise HTTPException(
            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
            detail=str(
                error,
            ),
        ) from error

    return SaveAnalysisVisualizationArtifactResponse(
        document_id=str(
            document_id,
        ),
        pages_count=len(
            pages,
        ),
    )
