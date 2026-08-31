# services/api-gateway/src/pdrd_api_gateway/core/container.py

"""Composition root микросервиса API Gateway."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial

from pdrd_api_gateway.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_api_gateway.application.use_cases.create_analysis_job import (
    CreateAnalysisJob,
)
from pdrd_api_gateway.application.use_cases.get_analysis_job import (
    GetAnalysisJob,
)
from pdrd_api_gateway.core.settings import Settings, get_settings
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
from pdrd_api_gateway.infrastructure.messaging.broker import (
    RabbitMqReadinessProbe,
    build_broker_url,
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

    async def close(self) -> None:
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
        timeout_seconds=(settings.database.health_timeout_seconds),
    )

    broker_url = build_broker_url(
        settings.broker,
    )

    broker_readiness = RabbitMqReadinessProbe(
        broker_url=broker_url,
        connect_timeout_seconds=(settings.broker.connect_timeout_seconds),
        health_timeout_seconds=(settings.broker.health_timeout_seconds),
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

    async def _shutdown_database() -> None:
        await engine.dispose()

    return ApplicationContainer(
        settings=settings,
        check_readiness=check_readiness,
        shutdown_callback=_shutdown_database,
        create_analysis_job=create_analysis_job,
        get_analysis_job=get_analysis_job,
    )
