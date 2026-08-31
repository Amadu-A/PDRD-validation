# services/knowledge-service/src/pdrd_knowledge_service/transport/http/dependencies.py

"""FastAPI dependencies Knowledge Service."""

from fastapi import Request

from pdrd_knowledge_service.core.container import (
    ApplicationContainer,
)


def get_container(
    request: Request,
) -> ApplicationContainer:
    """Возвращает composition container приложения."""
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
            "Knowledge Service container is not configured.",
        )

    return container
