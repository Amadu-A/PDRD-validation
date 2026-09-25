# services/experience-service/tests/unit/test_http_bootstrap.py

"""Модульные проверки HTTP-приложения Experience Service.

Назначение файла:
- проверять liveness и readiness без настоящего PostgreSQL;
- подтверждать отсутствие обращений к БД у выключенного сервиса;
- проверять Dependency Injection через подменяемый контейнер;
- контролировать освобождение ресурсов при остановке FastAPI;
- запрещать преждевременную публикацию бизнес-маршрутов.

Тесты не открывают сетевые соединения с PostgreSQL.

Для проверки зарегистрированных маршрутов используем
генерацию OpenAPI. Это позволяет корректно обрабатывать
вложенные маршрутизаторы современных версий FastAPI.
"""

from collections.abc import Awaitable, Callable

from fastapi.testclient import TestClient
from pdrd_experience_service.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_experience_service.core.container import (
    ApplicationContainer,
    build_container,
)
from pdrd_experience_service.core.settings import (
    DatabaseSettings,
    Settings,
)
from pdrd_experience_service.main import create_app


class FakeDatabaseProbe:
    """Подменяет PostgreSQL и фиксирует обращения к проверке готовности."""

    def __init__(
        self,
        ready: bool,
    ) -> None:
        """Задаёт состояние готовности тестового хранилища."""
        self.ready = ready
        self.calls = 0

    async def is_ready(self) -> bool:
        """Возвращает заданное состояние и учитывает вызов."""
        self.calls += 1
        return self.ready


def make_container(
    *,
    enabled: bool,
    probe: FakeDatabaseProbe,
    shutdown: Callable[[], Awaitable[None]],
) -> ApplicationContainer:
    """Создаёт изолированный контейнер для HTTP-тестирования.

    Передаём тестовые настройки и подменяем инфраструктурные
    зависимости, чтобы полностью исключить реальный PostgreSQL.
    """
    settings = Settings(
        _env_file=None,
        enabled=enabled,
        database=DatabaseSettings(
            password="test-private-password",
        ),
    )

    return ApplicationContainer(
        settings=settings,
        check_readiness=CheckReadiness(
            database=probe,
        ),
        shutdown_callback=shutdown,
    )


def test_disabled_service_has_liveness_but_no_readiness() -> None:
    """Выключенный сервис отвечает на live, но не обращается к PostgreSQL."""
    probe = FakeDatabaseProbe(
        ready=True,
    )

    async def shutdown() -> None:
        """Завершает тестовое приложение."""

    application = create_app(
        make_container(
            enabled=False,
            probe=probe,
            shutdown=shutdown,
        )
    )

    with TestClient(application) as client:
        live = client.get(
            "/health/live",
        )

        ready = client.get(
            "/health/ready",
        )

    assert live.status_code == 200
    assert live.json()["status"] == "alive"

    assert ready.status_code == 503
    assert ready.json()["detail"]["status"] == "disabled"

    # Даже имитация PostgreSQL не должна вызываться.
    assert probe.calls == 0


def test_enabled_service_checks_real_readiness_port() -> None:
    """Включённый сервис проверяет готовность через application port."""
    probe = FakeDatabaseProbe(
        ready=True,
    )

    async def shutdown() -> None:
        """Завершает тестовый контейнер."""

    application = create_app(
        make_container(
            enabled=True,
            probe=probe,
            shutdown=shutdown,
        )
    )

    with TestClient(application) as client:
        response = client.get(
            "/health/ready",
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"

    # Запрос проходит через CheckReadiness.
    assert probe.calls == 1


def test_unavailable_database_returns_503() -> None:
    """Недоступный PostgreSQL не должен считаться готовым сервисом."""
    probe = FakeDatabaseProbe(
        ready=False,
    )

    async def shutdown() -> None:
        """Завершает тестовую проверку."""

    application = create_app(
        make_container(
            enabled=True,
            probe=probe,
            shutdown=shutdown,
        )
    )

    with TestClient(application) as client:
        response = client.get(
            "/health/ready",
        )

    assert response.status_code == 503
    assert response.json()["detail"]["status"] == "database_unavailable"

    assert probe.calls == 1


def test_http_shutdown_releases_resources() -> None:
    """При остановке FastAPI должны освобождаться ресурсы контейнера."""
    closed: list[bool] = []

    async def shutdown() -> None:
        """Фиксирует вызов закрытия инфраструктурных ресурсов."""
        closed.append(True)

    application = create_app(
        make_container(
            enabled=False,
            probe=FakeDatabaseProbe(
                ready=False,
            ),
            shutdown=shutdown,
        )
    )

    with TestClient(application) as client:
        assert (
            client.get(
                "/health/live",
            ).status_code
            == 200
        )

    assert closed == [True]


def test_disabled_container_does_not_create_postgresql_engine(
    monkeypatch,
) -> None:
    """При выключенном сервисе не должен создаваться даже SQLAlchemy engine."""

    def forbidden_engine(settings):
        """Любая попытка создания engine считается ошибкой теста."""
        raise AssertionError(
            "Выключенный сервис не должен создавать подключение PostgreSQL."
        )

    monkeypatch.setattr(
        "pdrd_experience_service.core.container.build_async_engine",
        forbidden_engine,
    )

    container = build_container(
        Settings(
            _env_file=None,
            enabled=False,
        )
    )

    assert container.settings.enabled is False


def test_only_health_routes_exist_before_authorization() -> None:
    """До реализации авторизации доступны только системные HTTP-маршруты.

    В современных версиях FastAPI application.routes может
    содержать вспомогательные объекты _IncludedRouter.

    Они не обязаны иметь атрибут path. Поэтому проверяем
    фактически зарегистрированные HTTP-операции через OpenAPI.

    Swagger и публичный /openapi.json остаются выключенными.
    """

    async def shutdown() -> None:
        """Завершает тестовое приложение."""

    application = create_app(
        make_container(
            enabled=False,
            probe=FakeDatabaseProbe(
                ready=False,
            ),
            shutdown=shutdown,
        )
    )

    # Генерация схемы напрямую не включает публичный HTTP endpoint.
    paths = set(application.openapi()["paths"])

    assert paths == {
        "/health/live",
        "/health/ready",
    }

    # OpenAPI и Swagger не должны быть доступны через HTTP.
    with TestClient(application) as client:
        assert (
            client.get(
                "/openapi.json",
            ).status_code
            == 404
        )

        assert (
            client.get(
                "/docs",
            ).status_code
            == 404
        )
