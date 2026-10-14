# services/knowledge-service/src/pdrd_knowledge_service/main.py

"""HTTP-точка входа Knowledge: внедрение сценариев, маршруты и наблюдаемость E.

Инфраструктуру создаёт composition root; HTTP не управляет каталогом Experience
или shared-моделями. Рабочий E допускается конфигурацией и отчётом качества.
"""

from fastapi import FastAPI

from pdrd_knowledge_service.core.container import (
    ApplicationContainer,
    build_container,
)
from pdrd_knowledge_service.core.observability import configure_experience_logging
from pdrd_knowledge_service.transport.http.routers.document_context import (
    router as document_context_router,
)
from pdrd_knowledge_service.transport.http.routers.equipment_facts import (
    router as equipment_facts_router,
)
from pdrd_knowledge_service.transport.http.routers.health import (
    router as health_router,
)
from pdrd_knowledge_service.transport.http.routers.normative_categories import (
    router as normative_categories_router,
)
from pdrd_knowledge_service.transport.http.routers.normative_documents import (
    router as normative_documents_router,
)
from pdrd_knowledge_service.transport.http.routers.normative_sections import (
    router as normative_sections_router,
)
from pdrd_knowledge_service.transport.http.routers.project_context import (
    router as project_context_router,
)
from pdrd_knowledge_service.transport.http.routers.search import (
    router as search_router,
)
from pdrd_knowledge_service.transport.http.routers.technical_assignments import (
    router as technical_assignments_router,
)


def create_app(
    container: ApplicationContainer | None = None,
) -> FastAPI:
    """Создаёт HTTP-приложение с внедрёнными сценариями и стабильными timing-логгерами."""
    application_container = container if container is not None else build_container()
    configure_experience_logging()

    settings = application_container.settings

    application = FastAPI(
        title=settings.service_name,
        version=settings.service_version,
        docs_url=("/docs" if settings.docs_enabled else None),
        redoc_url=("/redoc" if settings.docs_enabled else None),
        openapi_url=("/openapi.json" if settings.docs_enabled else None),
    )

    application.state.container = application_container

    application.include_router(
        health_router,
    )

    application.include_router(
        search_router,
    )

    application.include_router(
        project_context_router,
    )

    application.include_router(document_context_router)
    application.include_router(equipment_facts_router)

    application.include_router(
        normative_sections_router,
    )

    application.include_router(
        normative_categories_router,
    )

    application.include_router(
        normative_documents_router,
    )

    application.include_router(
        technical_assignments_router,
    )

    return application


app = create_app()
