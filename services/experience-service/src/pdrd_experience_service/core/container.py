# services/experience-service/src/pdrd_experience_service/core/container.py

"""Сборка зависимостей и управление ресурсами Experience Service.

Назначение файла:
- создавать единый PostgreSQL Engine и фабрику независимых сессий;
- подключать существующие репозитории к application use cases;
- предоставлять готовые сценарии Human Review и подтверждения областей;
- не создавать подключения, если Experience Service выключен;
- освобождать ресурсы PostgreSQL при остановке приложения.

Открытие Review требует доверенного источника исходного анализа.
Если такой источник не передан, OpenReview не регистрируется.

Наличие бизнес-сценария в контейнере не означает разрешения
на его вызов по HTTP. Проверку пользователя и прав доступа
должен обеспечивать отдельный защищённый транспортный слой.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pdrd_experience_service.application.ports.analysis_source import (
    AnalysisSourceReader,
)
from pdrd_experience_service.application.ports.review import (
    ReviewRepository,
)
from pdrd_experience_service.application.use_cases.approval_experience import (
    ApprovalExperience,
)
from pdrd_experience_service.application.use_cases.capture_experience import (
    CaptureExperience,
)
from pdrd_experience_service.application.use_cases.catalog import ManageCatalog
from pdrd_experience_service.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_experience_service.application.use_cases.confirm_areas import (
    ConfirmArea,
    RevokeArea,
)
from pdrd_experience_service.application.use_cases.export_catalog import ExportCatalog
from pdrd_experience_service.application.use_cases.export_review import ExportReview
from pdrd_experience_service.application.use_cases.review import (
    ChangeReview,
    OpenReview,
)
from pdrd_experience_service.application.use_cases.select_experience import (
    SelectExperience,
)
from pdrd_experience_service.core.settings import (
    Settings,
    get_settings,
)
from pdrd_experience_service.infrastructure.analysis.completed_reader import (
    VerifiedCompletedAnalysisReader,
)
from pdrd_experience_service.infrastructure.analysis.http_source import (
    GatewayAnalysisSource,
)
from pdrd_experience_service.infrastructure.crops import (
    DocumentCropRenderer,
    LocalCropStore,
)
from pdrd_experience_service.infrastructure.database.catalog import (
    SqlAlchemyCatalogRepository,
)
from pdrd_experience_service.infrastructure.database.confirmed_areas import (
    SqlAlchemyConfirmedAreasRepository,
)
from pdrd_experience_service.infrastructure.database.engine import (
    build_async_engine,
    build_session_factory,
)
from pdrd_experience_service.infrastructure.database.health import (
    DatabaseReadinessProbe,
)
from pdrd_experience_service.infrastructure.database.repository import (
    SqlAlchemyReviewRepository,
)

ShutdownCallback = Callable[[], Awaitable[None]]


class DisabledDatabaseProbe:
    """Отклоняет готовность выключенного Experience Service."""

    async def is_ready(self) -> bool:
        """Не выполняет запросов к PostgreSQL."""
        return False


@dataclass(frozen=True, slots=True)
class ApplicationContainer:
    """Общий контейнер зависимостей одного экземпляра приложения.

    Первые три поля сохраняют существующий контракт.
    Дополнительные зависимости необязательны, поэтому прежние
    unit-тесты могут по-прежнему подменять только readiness.
    """

    settings: Settings
    check_readiness: CheckReadiness
    shutdown_callback: ShutdownCallback

    reviews: ReviewRepository | None = None
    confirmed_areas: SqlAlchemyConfirmedAreasRepository | None = None

    open_review: OpenReview | None = None
    change_review: ChangeReview | None = None
    confirm_area: ConfirmArea | None = None
    revoke_area: RevokeArea | None = None
    select_experience: SelectExperience | None = None
    export_review: ExportReview | None = None
    capture_experience: CaptureExperience | None = None
    catalog: ManageCatalog | None = None
    export_catalog: ExportCatalog | None = None
    approval_experience: ApprovalExperience | None = None

    async def close(self) -> None:
        """Освобождает ресурсы, созданные Composition Root."""
        await self.shutdown_callback()


def build_container(
    settings: Settings | None = None,
    *,
    analysis_source: AnalysisSourceReader | None = None,
) -> ApplicationContainer:
    """Собирает зависимости из конфигурации и доверенных адаптеров.

    При enabled=false возвращает только выключенный readiness.
    PostgreSQL Engine и бизнес-сценарии не создаются.

    При enabled=true создаёт один Engine и одну фабрику AsyncSession.
    Каждая операция репозитория самостоятельно открывает сессию.

    OpenReview получает явно переданный AnalysisSourceReader либо закрытый
    GatewayAnalysisSource при review_api_enabled=true. Сам Composition Root
    не загружает исходные документы и не открывает Review.
    """
    actual_settings = settings if settings is not None else get_settings()

    if not actual_settings.enabled:

        async def shutdown_disabled() -> None:
            """У выключенного сервиса нет ресурсов PostgreSQL."""

        return ApplicationContainer(
            settings=actual_settings,
            check_readiness=CheckReadiness(
                database=DisabledDatabaseProbe(),
            ),
            shutdown_callback=shutdown_disabled,
        )

    engine = build_async_engine(
        actual_settings.database,
    )

    session_factory = build_session_factory(
        engine,
    )

    reviews = SqlAlchemyReviewRepository(
        session_factory,
    )

    confirmed_areas = SqlAlchemyConfirmedAreasRepository(
        session_factory,
        reviews,
    )

    change_review = ChangeReview(
        repository=reviews,
    )

    confirm_area = ConfirmArea(
        reviews=reviews,
        areas=confirmed_areas,
    )

    revoke_area = RevokeArea(
        reviews=reviews,
        areas=confirmed_areas,
    )

    select_experience = SelectExperience(
        reviews=reviews,
        areas=confirmed_areas,
    )

    open_review: OpenReview | None = None

    if analysis_source is not None:
        verified_reader = VerifiedCompletedAnalysisReader(
            source=analysis_source,
        )

        open_review = OpenReview(
            analyses=verified_reader,
            repository=reviews,
        )

    elif actual_settings.review_api_enabled:
        open_review = OpenReview(
            analyses=VerifiedCompletedAnalysisReader(
                source=GatewayAnalysisSource(
                    base_url=actual_settings.gateway_base_url,
                    internal_key=actual_settings.internal_key.get_secret_value(),
                )
            ),
            repository=reviews,
        )

    readiness = DatabaseReadinessProbe(
        engine=engine,
        timeout_seconds=(actual_settings.database.connect_timeout_seconds),
    )
    catalog_repository = SqlAlchemyCatalogRepository(session_factory)
    crop_store = LocalCropStore(actual_settings.crop_root)
    capture_source = analysis_source or (
        GatewayAnalysisSource(
            base_url=actual_settings.gateway_base_url,
            internal_key=actual_settings.internal_key.get_secret_value(),
        )
        if actual_settings.review_api_enabled
        else None
    )
    capture = (
        CaptureExperience(
            reviews,
            confirmed_areas,
            capture_source,
            DocumentCropRenderer(actual_settings.document_base_url),
            crop_store,
            catalog_repository,
        )
        if capture_source is not None
        else None
    )

    async def shutdown_database() -> None:
        """Корректно освобождает общий пул PostgreSQL."""
        await engine.dispose()

    return ApplicationContainer(
        settings=actual_settings,
        check_readiness=CheckReadiness(
            database=readiness,
        ),
        shutdown_callback=shutdown_database,
        reviews=reviews,
        confirmed_areas=confirmed_areas,
        open_review=open_review,
        change_review=change_review,
        confirm_area=confirm_area,
        revoke_area=revoke_area,
        select_experience=select_experience,
        export_review=ExportReview(reviews=reviews, areas=confirmed_areas),
        catalog=ManageCatalog(catalog_repository, crop_store),
        export_catalog=ExportCatalog(catalog_repository, crop_store),
        capture_experience=capture,
        approval_experience=ApprovalExperience(capture)
        if capture is not None
        else None,
    )
