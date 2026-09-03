# services/api-gateway/src/pdrd_api_gateway/core/container.py

"""Composition root микросервиса API Gateway."""

from collections.abc import (
    Awaitable,
    Callable,
)
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from pdrd_api_gateway.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_api_gateway.application.use_cases.create_analysis_job import (
    CreateAnalysisJob,
)
from pdrd_api_gateway.application.use_cases.get_analysis_job import (
    GetAnalysisJob,
)
from pdrd_api_gateway.application.use_cases.get_analysis_result import (
    GetAnalysisResult,
)
from pdrd_api_gateway.application.use_cases.manage_normative_catalog import (
    NormativeCatalogFacade,
)
from pdrd_api_gateway.application.use_cases.resolve_normative_snapshot import (
    ResolveNormativeSnapshot,
)
from pdrd_api_gateway.application.use_cases.submit_analysis import (
    SubmitAnalysis,
)
from pdrd_api_gateway.core.settings import (
    Settings,
    get_settings,
)
from pdrd_api_gateway.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_api_gateway.infrastructure.database.health import (
    DatabaseReadinessProbe,
)
from pdrd_api_gateway.infrastructure.database.unit_of_work import (
    SqlAlchemyUnitOfWork,
)
from pdrd_api_gateway.infrastructure.knowledge.normative_catalog import (
    HttpNormativeCatalogReader,
)
from pdrd_api_gateway.infrastructure.knowledge.normative_catalog_management import (
    HttpNormativeCatalogManager,
)
from pdrd_api_gateway.infrastructure.messaging.broker import (
    RabbitMqReadinessProbe,
    build_broker_url,
)
from pdrd_api_gateway.infrastructure.storage.filesystem import (
    LocalFilesystemAnalysisArtifactStore,
)

ShutdownCallback = Callable[
    [],
    Awaitable[None],
]


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Хранит runtime dependencies API Gateway."""

    settings: Settings

    check_readiness: CheckReadiness

    shutdown_callback: ShutdownCallback

    create_analysis_job: CreateAnalysisJob | None = None

    get_analysis_job: GetAnalysisJob | None = None

    get_analysis_result: GetAnalysisResult | None = None

    submit_analysis: SubmitAnalysis | None = None

    normative_catalog: NormativeCatalogFacade | None = None

    async def close(
        self,
    ) -> None:
        """Корректно освобождает infrastructure resources."""
        await self.shutdown_callback()


def build_container() -> ApplicationContainer:
    """Собирает production dependencies API Gateway."""
    settings = get_settings()

    engine = build_async_engine(
        settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    unit_of_work_factory = partial(
        SqlAlchemyUnitOfWork,
        session_factory,
    )

    database_readiness = DatabaseReadinessProbe(
        engine=engine,
        timeout_seconds=settings.database.health_timeout_seconds,
    )

    broker_url = build_broker_url(
        settings.broker,
    )

    broker_readiness = RabbitMqReadinessProbe(
        broker_url=broker_url,
        connect_timeout_seconds=settings.broker.connect_timeout_seconds,
        health_timeout_seconds=settings.broker.health_timeout_seconds,
    )

    check_readiness = CheckReadiness(
        database=database_readiness,
        broker=broker_readiness,
    )

    create_analysis_job = CreateAnalysisJob(
        unit_of_work_factory=unit_of_work_factory,
    )

    get_analysis_job = GetAnalysisJob(
        unit_of_work_factory=unit_of_work_factory,
    )

    artifact_store = LocalFilesystemAnalysisArtifactStore(
        root_path=Path(
            settings.storage.root_path,
        ),
    )

    normative_catalog_reader = HttpNormativeCatalogReader(
        settings=settings.knowledge_service,
    )

    normative_catalog_manager = HttpNormativeCatalogManager(
        settings=settings.knowledge_service,
    )

    normative_catalog = NormativeCatalogFacade(
        manager=normative_catalog_manager,
    )

    resolve_normative_snapshot = ResolveNormativeSnapshot(
        catalog_reader=normative_catalog_reader,
    )

    submit_analysis = SubmitAnalysis(
        artifact_store=artifact_store,
        create_analysis_job=create_analysis_job,
        resolve_normative_snapshot=resolve_normative_snapshot,
    )

    get_analysis_result = GetAnalysisResult(
        get_analysis_job=get_analysis_job,
        artifact_store=artifact_store,
    )

    async def _shutdown_database() -> None:
        await engine.dispose()

    return ApplicationContainer(
        settings=settings,
        check_readiness=check_readiness,
        shutdown_callback=_shutdown_database,
        create_analysis_job=create_analysis_job,
        get_analysis_job=get_analysis_job,
        get_analysis_result=get_analysis_result,
        submit_analysis=submit_analysis,
        normative_catalog=normative_catalog,
    )
