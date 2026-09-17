# services/api-gateway/tests/integration/test_analysis_cancellation_endpoint.py

"""HTTP contract tests отмены analysis jobs."""

from collections.abc import (
    Awaitable,
    Callable,
)
from uuid import (
    UUID,
    uuid4,
)

from fastapi.testclient import TestClient
from pdrd_api_gateway.application.use_cases.cancel_analysis_job import (
    AnalysisJobCancellationConflictError,
    AnalysisJobCancellationNotFoundError,
)
from pdrd_api_gateway.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.core.settings import (
    DatabaseSettings,
    Settings,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisJob,
)
from pdrd_api_gateway.main import create_app


class StaticReadiness:
    """Fake infrastructure readiness."""

    async def is_ready(
        self,
    ) -> bool:
        """Всегда сообщает готовность."""
        return True


class CancelAnalysisStub:
    """Fake CancelAnalysisJob для HTTP contract tests."""

    def __init__(
        self,
        *,
        job: AnalysisJob | None = None,
        error: Exception | None = None,
    ) -> None:
        """Сохраняет ожидаемый result."""
        self._job = job
        self._error = error

        self.called_job_id: UUID | None = None

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> AnalysisJob:
        """Возвращает result либо поднимает настроенную ошибку."""
        self.called_job_id = job_id

        if self._error is not None:
            raise self._error

        if self._job is None:
            raise AssertionError(
                "CancelAnalysisStub job is not configured.",
            )

        return self._job


async def noop_shutdown() -> None:
    """Имитирует освобождение resources."""


def build_client(
    *,
    cancel_stub: CancelAnalysisStub,
) -> TestClient:
    """Создаёт HTTP client с fake cancellation use case."""
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
        cancel_analysis_job=cancel_stub,  # type: ignore[arg-type]
    )

    return TestClient(
        create_app(
            container=container,
        )
    )


def test_cancel_analysis_returns_cancelled_job() -> None:
    """Проверяет успешный HTTP cancellation contract."""
    job = AnalysisJob.create()
    job.mark_queued()
    job.mark_processing()
    job.mark_cancelled()

    cancel_stub = CancelAnalysisStub(
        job=job,
    )

    with build_client(
        cancel_stub=cancel_stub,
    ) as client:
        response = client.post(
            f"/api/v1/analyses/{job.id}/cancel",
        )

    assert response.status_code == 200

    assert response.json() == {
        "job_id": str(
            job.id,
        ),
        "status": "cancelled",
    }

    assert cancel_stub.called_job_id == job.id


def test_cancel_unknown_analysis_returns_404() -> None:
    """Проверяет HTTP 404 для неизвестного analysis job."""
    job_id = uuid4()

    cancel_stub = CancelAnalysisStub(
        error=AnalysisJobCancellationNotFoundError(
            f"Analysis job {job_id} not found.",
        ),
    )

    with build_client(
        cancel_stub=cancel_stub,
    ) as client:
        response = client.post(
            f"/api/v1/analyses/{job_id}/cancel",
        )

    assert response.status_code == 404

    assert response.json()["detail"] == (f"Analysis job {job_id} not found.")


def test_cancel_terminal_analysis_returns_409() -> None:
    """Проверяет HTTP 409 для уже завершённого analysis job."""
    job_id = uuid4()

    cancel_stub = CancelAnalysisStub(
        error=AnalysisJobCancellationConflictError(
            f"Analysis job {job_id} cannot be cancelled from status completed.",
        ),
    )

    with build_client(
        cancel_stub=cancel_stub,
    ) as client:
        response = client.post(
            f"/api/v1/analyses/{job_id}/cancel",
        )

    assert response.status_code == 409

    assert response.json()["detail"] == (
        f"Analysis job {job_id} cannot be cancelled from status completed."
    )
