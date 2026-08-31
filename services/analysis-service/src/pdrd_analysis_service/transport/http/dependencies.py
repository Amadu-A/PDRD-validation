# services/analysis-service/src/pdrd_analysis_service/transport/http/dependencies.py

"""FastAPI dependencies Analysis Service."""

from fastapi import Request

from pdrd_analysis_service.core.container import (
    ApplicationContainer,
)


def get_container(
    request: Request,
) -> ApplicationContainer:
    """Возвращает application container."""
    container = getattr(
        request.app.state,
        "container",
        None,
    )

    if not isinstance(
        container,
        ApplicationContainer,
    ):
        raise RuntimeError(
            "Analysis Service container is not configured.",
        )

    return container
