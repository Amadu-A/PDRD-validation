# services/api-gateway/tests/integration/test_analysis_result_endpoint.py

"""HTTP contract tests получения результата анализа."""

from collections.abc import (
    Awaitable,
    Callable,
)
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pdrd_api_gateway.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_api_gateway.application.use_cases.get_analysis_result import (
    AnalysisResultJobNotFoundError,
    AnalysisResultNotReadyError,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.core.settings import (
    DatabaseSettings,
    Settings,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJobStatus,
)
from pdrd_api_gateway.main import create_app


class StaticReadiness:
    """Fake infrastructure readiness."""

    async def is_ready(self) -> bool:
        """Всегда сообщает готовность."""
        return True


class GetAnalysisResultStub:
    """Fake GetAnalysisResult."""

    def __init__(
        self,
        *,
        result: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        """Сохраняет подготовленный result или error."""
        self._result = result
        self._error = error

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> dict[str, Any]:
        """Возвращает fake result."""
        if self._error is not None:
            raise self._error

        assert self._result is not None
        assert isinstance(
            job_id,
            UUID,
        )

        return self._result


async def noop_shutdown() -> None:
    """Имитирует освобождение resources."""


def build_client(
    *,
    result_stub: GetAnalysisResultStub,
) -> TestClient:
    """Создаёт client с fake result use case."""
    settings = Settings(
        _env_file=None,
        environment="test",
        database=DatabaseSettings(
            password="test-password",
        ),
    )

    check_readiness = CheckReadiness(
        database=StaticReadiness(),
        broker=StaticReadiness(),
    )

    shutdown_callback: Callable[
        [],
        Awaitable[None],
    ] = noop_shutdown

    container = ApplicationContainer(
        settings=settings,
        check_readiness=check_readiness,
        shutdown_callback=shutdown_callback,
        get_analysis_result=result_stub,  # type: ignore[arg-type]
    )

    return TestClient(
        create_app(
            container=container,
        )
    )


def test_get_completed_analysis_result() -> None:
    """Completed analysis возвращает итоговый JSON."""
    job_id = uuid4()

    expected = {
        "status": "completed",
        "source_mode": "pdf_only",
        "findings_count": 1,
        "findings": [
            {
                "severity": "warning",
                "category": "normative_control",
                "comment": "Тестовое замечание.",
            },
        ],
    }

    with build_client(
        result_stub=GetAnalysisResultStub(
            result=expected,
        ),
    ) as client:
        response = client.get(
            f"/api/v1/analyses/{job_id}/result",
        )

    assert response.status_code == 200
    assert response.json() == expected


def test_get_analysis_result_before_completion() -> None:
    """Незавершённый job возвращает HTTP 409."""
    job_id = uuid4()

    with build_client(
        result_stub=GetAnalysisResultStub(
            error=AnalysisResultNotReadyError(
                status=AnalysisJobStatus.PROCESSING,
            ),
        ),
    ) as client:
        response = client.get(
            f"/api/v1/analyses/{job_id}/result",
        )

    assert response.status_code == 409

    assert response.json()["detail"]["status"] == "processing"


def test_get_unknown_analysis_result() -> None:
    """Неизвестный job возвращает HTTP 404."""
    job_id = uuid4()

    with build_client(
        result_stub=GetAnalysisResultStub(
            error=AnalysisResultJobNotFoundError(
                "Analysis job не найден.",
            ),
        ),
    ) as client:
        response = client.get(
            f"/api/v1/analyses/{job_id}/result",
        )

    assert response.status_code == 404
