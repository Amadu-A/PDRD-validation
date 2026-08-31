# services/document-service/src/pdrd_document_service/transport/http/dependencies.py

"""FastAPI dependencies Document Service."""

from fastapi import Request

from pdrd_document_service.core.container import (
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
            "Document Service container is not configured.",
        )

    return container
