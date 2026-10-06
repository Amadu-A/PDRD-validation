# services/api-gateway/src/pdrd_api_gateway/main.py

"""Точка входа FastAPI-приложения API Gateway."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from pdrd_api_gateway.core.container import (
    ApplicationContainer,
    build_container,
)
from pdrd_api_gateway.core.observability import configure_review_logging
from pdrd_api_gateway.infrastructure.analysis_scope import (
    HttpUserAnalysisScopeChecker,
)
from pdrd_api_gateway.transport.http.analysis_access import (
    enforce_analysis_job_access,
)
from pdrd_api_gateway.transport.http.identity_authorization import (
    IdentityAuthorizer,
    enforce_identity_authorization,
)
from pdrd_api_gateway.transport.http.routers.admin_proxy import (
    router as admin_proxy_router,
)
from pdrd_api_gateway.transport.http.routers.analyses import (
    router as analyses_router,
)
from pdrd_api_gateway.transport.http.routers.analysis_artifacts import (
    router as analysis_artifacts_router,
)
from pdrd_api_gateway.transport.http.routers.analysis_cancellation import (
    router as analysis_cancellation_router,
)
from pdrd_api_gateway.transport.http.routers.analysis_pdf_exports import (
    router as analysis_pdf_exports_router,
)
from pdrd_api_gateway.transport.http.routers.analysis_progress import (
    router as analysis_progress_router,
)
from pdrd_api_gateway.transport.http.routers.auth_proxy import (
    router as auth_proxy_router,
)
from pdrd_api_gateway.transport.http.routers.experience import (
    router as experience_router,
)
from pdrd_api_gateway.transport.http.routers.experience_quality import (
    router as experience_quality_router,
)
from pdrd_api_gateway.transport.http.routers.experience_versions import (
    router as experience_versions_router,
)
from pdrd_api_gateway.transport.http.routers.health import (
    router as health_router,
)
from pdrd_api_gateway.transport.http.routers.normative_catalog import (
    router as normative_catalog_router,
)
from pdrd_api_gateway.transport.http.routers.project_context_preflight import (
    router as project_context_preflight_router,
)
from pdrd_api_gateway.transport.http.routers.review import router as review_router
from pdrd_api_gateway.transport.http.routers.review_source import (
    router as review_source_router,
)
from pdrd_api_gateway.transport.http.routers.technical_assignments import (
    router as technical_assignments_router,
)
from pdrd_api_gateway.transport.http.routers.user_packages import (
    router as user_packages_router,
)


def create_app(
    container: ApplicationContainer | None = None,
) -> FastAPI:
    """Создаёт настроенный экземпляр FastAPI."""
    application_container = container if container is not None else build_container()

    settings = application_container.settings
    if settings.review.enabled:
        configure_review_logging()

    @asynccontextmanager
    async def lifespan(
        application: FastAPI,
    ) -> AsyncIterator[None]:
        """Управляет lifecycle infrastructure resources."""
        del application

        try:
            yield

        finally:
            await application_container.close()

    docs_url = "/docs" if settings.docs_enabled else None

    redoc_url = "/redoc" if settings.docs_enabled else None

    openapi_url = "/openapi.json" if settings.docs_enabled else None

    application = FastAPI(
        title=settings.service_name,
        version=settings.service_version,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    application.state.container = application_container
    authorizer = (
        IdentityAuthorizer(settings.identity_proxy)
        if settings.identity_proxy.authorization_enabled
        else None
    )
    scope_checker = (
        HttpUserAnalysisScopeChecker(
            settings.identity_proxy.user_service_url,
            settings.identity_proxy.user_service_internal_key.get_secret_value(),
            timeout_seconds=settings.identity_proxy.timeout_seconds,
        )
        if authorizer is not None
        else None
    )
    application.state.identity_authorizer = authorizer

    @application.middleware("http")
    async def protect_identity_actions(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Запрещает закрытые операции до обращения к хранилищам и брокеру."""
        if authorizer is not None:
            denial = await enforce_identity_authorization(request, authorizer)
            if denial is not None:
                denial.headers["Cache-Control"] = "no-store"
                return denial
            path = request.url.path
            if (
                path.startswith("/api/v1/analyses")
                or path.startswith("/api/v1/experience/capture/")
                or path.startswith("/api/v1/normative/")
            ):
                denial = await authorizer.authenticate_if_present(request)
                if denial is None:
                    denial = await enforce_analysis_job_access(
                        request,
                        application_container.get_analysis_job,
                        request.state.identity_user_id,
                        scope_checker=scope_checker,
                    )
                if denial is not None:
                    denial.headers["Cache-Control"] = "no-store"
                    return denial
        response = await call_next(request)
        if authorizer is not None and request.url.path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    application.include_router(
        health_router,
    )

    application.include_router(review_router)
    application.include_router(experience_router)
    application.include_router(experience_versions_router)
    application.include_router(experience_quality_router)
    application.include_router(review_source_router)

    application.include_router(
        analyses_router,
    )

    application.include_router(
        analysis_cancellation_router,
    )

    application.include_router(
        analysis_progress_router,
    )

    application.include_router(
        analysis_artifacts_router,
    )

    application.include_router(
        analysis_pdf_exports_router,
    )

    application.include_router(
        project_context_preflight_router,
    )

    application.include_router(
        normative_catalog_router,
    )

    application.include_router(
        user_packages_router,
    )

    application.include_router(
        technical_assignments_router,
    )

    if settings.identity_proxy.enabled:
        application.include_router(auth_proxy_router)
        application.include_router(admin_proxy_router)

    return application


app = create_app()
