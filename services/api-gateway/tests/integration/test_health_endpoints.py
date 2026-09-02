# services/api-gateway/tests/integration/test_health_endpoints.py

"""API-тесты health endpoints нового API Gateway."""

from collections.abc import Awaitable, Callable

from fastapi.testclient import TestClient
from pdrd_api_gateway.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.core.settings import (
    DatabaseSettings,
    Settings,
)
from pdrd_api_gateway.main import create_app


class StaticDatabaseReadiness:
    """Fake database readiness adapter."""

    def __init__(
        self,
        ready: bool,
    ) -> None:
        """Сохраняет ожидаемый результат readiness."""
        self._ready = ready

    async def is_ready(self) -> bool:
        """Возвращает заранее заданное состояние."""
        return self._ready


class StaticBrokerReadiness:
    """Fake RabbitMQ readiness adapter."""

    def __init__(
        self,
        ready: bool,
    ) -> None:
        """Сохраняет ожидаемый результат readiness."""
        self._ready = ready

    async def is_ready(self) -> bool:
        """Возвращает заранее заданное состояние."""
        return self._ready


async def noop_shutdown() -> None:
    """Имитирует освобождение infrastructure resources."""


def build_test_client(
    *,
    docs_enabled: bool = True,
    database_ready: bool = True,
    broker_ready: bool = True,
) -> TestClient:
    """Создаёт TestClient с изолированными dependencies."""
    settings = Settings(
        _env_file=None,
        service_name="PDRD API Gateway Test",
        service_version="0.1.0-test",
        environment="test",
        docs_enabled=docs_enabled,
        database=DatabaseSettings(
            password="integration-test-password",
        ),
    )

    check_readiness = CheckReadiness(
        database=StaticDatabaseReadiness(
            ready=database_ready,
        ),
        broker=StaticBrokerReadiness(
            ready=broker_ready,
        ),
    )

    shutdown_callback: Callable[
        [],
        Awaitable[None],
    ] = noop_shutdown

    container = ApplicationContainer(
        settings=settings,
        check_readiness=check_readiness,
        shutdown_callback=shutdown_callback,
    )

    return TestClient(
        create_app(
            container=container,
        )
    )


def test_liveness_endpoint() -> None:
    """Проверяет status code и contract liveness endpoint."""
    with build_test_client() as client:
        response = client.get(
            "/health/live",
        )

    assert response.status_code == 200

    assert response.json() == {
        "status": "ok",
        "service": "PDRD API Gateway Test",
        "version": "0.1.0-test",
    }


def test_readiness_endpoint() -> None:
    """Проверяет успешный readiness contract."""
    with build_test_client() as client:
        response = client.get(
            "/health/ready",
        )

    assert response.status_code == 200

    assert response.json() == {
        "status": "ready",
        "service": "PDRD API Gateway Test",
        "version": "0.1.0-test",
        "environment": "test",
        "dependencies": {
            "database": "ok",
            "broker": "ok",
        },
    }


def test_readiness_returns_503_when_database_is_unavailable() -> None:
    """Проверяет отказ readiness при недоступном PostgreSQL."""
    with build_test_client(
        database_ready=False,
    ) as client:
        response = client.get(
            "/health/ready",
        )

    assert response.status_code == 503

    assert response.json() == {
        "detail": {
            "database": "unavailable",
            "broker": "ok",
        }
    }


def test_readiness_returns_503_when_broker_is_unavailable() -> None:
    """Проверяет отказ readiness при недоступном RabbitMQ."""
    with build_test_client(
        broker_ready=False,
    ) as client:
        response = client.get(
            "/health/ready",
        )

    assert response.status_code == 503

    assert response.json() == {
        "detail": {
            "database": "ok",
            "broker": "unavailable",
        }
    }


def test_api_documentation_can_be_disabled() -> None:
    """Проверяет отключение Swagger UI через Settings."""
    with build_test_client(
        docs_enabled=False,
    ) as client:
        docs_response = client.get(
            "/docs",
        )

        openapi_response = client.get(
            "/openapi.json",
        )

    assert docs_response.status_code == 404
    assert openapi_response.status_code == 404
