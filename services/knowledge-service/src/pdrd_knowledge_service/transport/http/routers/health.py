# services/knowledge-service/src/pdrd_knowledge_service/transport/http/routers/health.py

"""Health endpoints Knowledge Service."""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)

from pdrd_knowledge_service.core.container import (
    ApplicationContainer,
)
from pdrd_knowledge_service.transport.http.dependencies import (
    get_container,
)
from pdrd_knowledge_service.transport.http.schemas.health import (
    LiveHealthResponse,
    ReadyHealthResponse,
)

router = APIRouter(
    tags=["health"],
)


@router.get(
    "/health/live",
    response_model=LiveHealthResponse,
)
def health_live(
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> LiveHealthResponse:
    """Возвращает liveness процесса."""
    settings = container.settings

    return LiveHealthResponse(
        service=settings.service_name,
        version=settings.service_version,
    )


@router.get(
    "/health/ready",
    response_model=ReadyHealthResponse,
)
async def health_ready(
    container: Annotated[
        ApplicationContainer,
        Depends(get_container),
    ],
) -> ReadyHealthResponse:
    """Проверяет embedding model и Qdrant collections."""
    report = await container.check_readiness.execute()

    dependencies = {
        "embedding_model": (report.embedding_model),
        "qdrant": report.qdrant,
        "normative_collection": (report.normative_collection),
        "experience_collection": (report.experience_collection),
    }

    if not report.ready:
        raise HTTPException(
            status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
            detail={
                "status": "not_ready",
                "dependencies": dependencies,
            },
        )

    settings = container.settings

    return ReadyHealthResponse(
        service=settings.service_name,
        version=settings.service_version,
        dependencies=dependencies,
    )
