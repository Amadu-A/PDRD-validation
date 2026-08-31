# services/api-gateway/tests/integration/test_health_endpoints.py

"""API-тесты health endpoints нового API Gateway."""

from fastapi.testclient import TestClient
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.core.settings import Settings
from pdrd_api_gateway.main import create_app


def build_test_client(
    *,
    docs_enabled: bool = True,
) -> TestClient:
    """Создаёт TestClient с изолированной test-конфигурацией.

    Args:
        docs_enabled: Нужно ли включить OpenAPI UI в тестовом приложении.

    Returns:
        HTTP test client без запуска реального web server.
    """
    settings = Settings(
        _env_file=None,
        service_name="PDRD API Gateway Test",
        service_version="0.1.0-test",
        environment="test",
        docs_enabled=docs_enabled,
    )

    container = ApplicationContainer(
        settings=settings,
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
    """Проверяет status code и contract readiness endpoint."""
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
    }


def test_api_documentation_can_be_disabled() -> None:
    """Проверяет отключение служебной Swagger UI через Settings."""
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
