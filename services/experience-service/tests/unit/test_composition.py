# services/experience-service/tests/unit/test_composition.py

"""Модульные проверки сборки зависимостей Experience Service.

Назначение файла:
- контролировать создание единого PostgreSQL Engine;
- проверять общие экземпляры репозиториев для всех use cases;
- не допускать создания Engine при выключенном сервисе;
- проверять, что OpenReview требует доверенный источник анализа;
- контролировать освобождение ресурсов при остановке;
- запрещать публикацию бизнес-маршрутов до внедрения авторизации.

Тесты используют подменяемый Engine и не подключаются к БД.
"""

from uuid import UUID

import pytest
from pdrd_experience_service.application.ports.analysis_source import (
    CompletedAnalysisArtifacts,
)
from pdrd_experience_service.core import (
    container as container_module,
)
from pdrd_experience_service.core.container import (
    build_container,
)
from pdrd_experience_service.core.settings import (
    DatabaseSettings,
    Settings,
)
from pdrd_experience_service.infrastructure.analysis.completed_reader import (
    VerifiedCompletedAnalysisReader,
)
from pdrd_experience_service.infrastructure.database.confirmed_areas import (
    SqlAlchemyConfirmedAreasRepository,
)
from pdrd_experience_service.infrastructure.database.repository import (
    SqlAlchemyReviewRepository,
)
from pdrd_experience_service.main import create_app


class FakeEngine:
    """Имитирует жизненный цикл Engine без сетевых соединений."""

    def __init__(self) -> None:
        """Создаёт счётчик освобождения ресурсов."""
        self.disposals = 0

    async def dispose(self) -> None:
        """Фиксирует освобождение тестового Engine."""
        self.disposals += 1


class FakeAnalysisSource:
    """Имитирует доверенный источник без загрузки документов."""

    def __init__(self) -> None:
        """Создаёт счётчик обращений к источнику."""
        self.calls = 0

    async def load_completed(
        self,
        job_id: UUID,
    ) -> CompletedAnalysisArtifacts:
        """Запрещает загрузку анализа во время сборки зависимостей."""
        self.calls += 1

        raise AssertionError("Composition Root не должен загружать анализ.")


def enabled_settings() -> Settings:
    """Создаёт активную тестовую конфигурацию с отдельным паролем."""
    return Settings(
        _env_file=None,
        environment="test",
        enabled=True,
        database=DatabaseSettings(
            host="127.0.0.1",
            name="composition_test",
            user="composition_test",
            password="isolated-test-password",
        ),
    )


def disabled_settings() -> Settings:
    """Создаёт явно выключенную конфигурацию."""
    return Settings(
        _env_file=None,
        environment="test",
        enabled=False,
    )


def install_fake_database(
    monkeypatch: pytest.MonkeyPatch,
    engine: FakeEngine,
) -> None:
    """Подменяет создание ресурсов, сохраняя настоящую DI-композицию.

    Реальные классы репозиториев и use cases продолжают создаваться.
    Заменяется только инфраструктура, которая могла бы открыть сокет.
    """

    def session_factory() -> None:
        """Имитирует получение сессии без подключения к PostgreSQL."""

    def fake_build_engine(
        settings: DatabaseSettings,
    ) -> FakeEngine:
        """Возвращает заранее созданный тестовый Engine."""
        assert settings.name == "composition_test"
        return engine

    def fake_build_session_factory(
        actual_engine: FakeEngine,
    ):
        """Проверяет передачу правильного Engine фабрике сессий."""
        assert actual_engine is engine
        return session_factory

    monkeypatch.setattr(
        container_module,
        "build_async_engine",
        fake_build_engine,
    )

    monkeypatch.setattr(
        container_module,
        "build_session_factory",
        fake_build_session_factory,
    )


@pytest.mark.asyncio
async def test_disabled_service_does_not_construct_business_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Выключенный сервис не создаёт ни Engine, ни бизнес-сценарии."""

    def forbidden_engine(
        settings: DatabaseSettings,
    ) -> None:
        """Любое обращение к инфраструктуре считается ошибкой."""
        raise AssertionError(
            "Выключенный сервис не должен создавать PostgreSQL Engine."
        )

    monkeypatch.setattr(
        container_module,
        "build_async_engine",
        forbidden_engine,
    )

    source = FakeAnalysisSource()

    container = build_container(
        disabled_settings(),
        analysis_source=source,
    )

    assert container.reviews is None
    assert container.confirmed_areas is None

    assert container.open_review is None
    assert container.change_review is None
    assert container.confirm_area is None
    assert container.revoke_area is None
    assert container.select_experience is None

    assert not await container.check_readiness.execute()

    await container.close()

    assert source.calls == 0


@pytest.mark.asyncio
async def test_enabled_service_wires_existing_repositories_and_use_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Все сценарии должны использовать общие DI-зависимости."""
    engine = FakeEngine()

    install_fake_database(
        monkeypatch,
        engine,
    )

    source = FakeAnalysisSource()

    container = build_container(
        enabled_settings(),
        analysis_source=source,
    )

    try:
        reviews = container.reviews
        areas = container.confirmed_areas

        assert isinstance(
            reviews,
            SqlAlchemyReviewRepository,
        )

        assert isinstance(
            areas,
            SqlAlchemyConfirmedAreasRepository,
        )

        assert container.change_review is not None
        assert container.confirm_area is not None
        assert container.revoke_area is not None
        assert container.select_experience is not None
        assert container.open_review is not None

        assert container.change_review.repository is reviews

        assert container.confirm_area.reviews is reviews
        assert container.confirm_area.areas is areas

        assert container.revoke_area.reviews is reviews
        assert container.revoke_area.areas is areas

        assert container.select_experience.reviews is reviews
        assert container.select_experience.areas is areas

        assert container.open_review.repository is reviews

        reader = container.open_review.analyses

        assert isinstance(
            reader,
            VerifiedCompletedAnalysisReader,
        )

        assert reader.source is source

        # Сборка зависимостей не должна загружать документы.
        assert source.calls == 0

    finally:
        await container.close()

    assert engine.disposals == 1


@pytest.mark.asyncio
async def test_open_review_requires_an_explicit_trusted_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без доверенного адаптера открытие Review не регистрируется."""
    engine = FakeEngine()

    install_fake_database(
        monkeypatch,
        engine,
    )

    container = build_container(
        enabled_settings(),
    )

    try:
        assert container.open_review is None

        assert container.change_review is not None
        assert container.confirm_area is not None
        assert container.revoke_area is not None

    finally:
        await container.close()

    assert engine.disposals == 1


@pytest.mark.asyncio
async def test_di_does_not_publish_unauthorized_business_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Регистрация use cases не открывает операции для HTTP-клиентов."""
    engine = FakeEngine()

    install_fake_database(
        monkeypatch,
        engine,
    )

    container = build_container(
        enabled_settings(),
        analysis_source=FakeAnalysisSource(),
    )

    try:
        application = create_app(
            container,
        )

        assert set(application.openapi()["paths"]) == {
            "/health/live",
            "/health/ready",
        }

        assert container.confirm_area is not None

    finally:
        await container.close()

    assert engine.disposals == 1
