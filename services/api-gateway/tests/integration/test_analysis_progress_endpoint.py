# services/api-gateway/tests/integration/test_analysis_progress_endpoint.py

"""HTTP contract tests internal analysis progress checkpoints."""

from collections.abc import (
    Awaitable,
    Callable,
)
from uuid import (
    UUID,
    uuid4,
)

from fastapi.testclient import TestClient
from pdrd_api_gateway.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_api_gateway.application.use_cases.update_analysis_progress import (
    AnalysisProgressJobNotFoundError,
    AnalysisProgressUpdateResult,
)
from pdrd_api_gateway.core.container import (
    ApplicationContainer,
)
from pdrd_api_gateway.core.settings import (
    DatabaseSettings,
    Settings,
)
from pdrd_api_gateway.domain.analysis_job import (
    AnalysisProgressStage,
)
from pdrd_api_gateway.main import create_app


class StaticReadiness:
    """Fake infrastructure readiness."""

    async def is_ready(
        self,
    ) -> bool:
        """Всегда сообщает готовность."""
        return True


class UpdateAnalysisProgressStub:
    """Fake UpdateAnalysisProgress для HTTP contract tests."""

    def __init__(
        self,
        *,
        result: AnalysisProgressUpdateResult | None = None,
        error: Exception | None = None,
    ) -> None:
        """Сохраняет ожидаемый result или error."""
        self._result = result
        self._error = error

        self.called_document_id: UUID | None = None
        self.called_stage: AnalysisProgressStage | None = None

    async def execute(
        self,
        *,
        document_id: UUID,
        stage: AnalysisProgressStage,
    ) -> AnalysisProgressUpdateResult:
        """Возвращает настроенный checkpoint result."""
        self.called_document_id = document_id
        self.called_stage = stage

        if self._error is not None:
            raise self._error

        if self._result is None:
            raise AssertionError(
                "UpdateAnalysisProgressStub result is not configured.",
            )

        return self._result


async def noop_shutdown() -> None:
    """Имитирует освобождение resources."""


def build_client(
    *,
    progress_stub: UpdateAnalysisProgressStub,
) -> TestClient:
    """Создаёт HTTP client с fake progress use case."""
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
        update_analysis_progress=progress_stub,  # type: ignore[arg-type]
    )

    return TestClient(
        create_app(
            container=container,
        )
    )


def test_progress_checkpoint_returns_continue_signal() -> None:
    """Обычный callback сообщает n8n, что analysis можно продолжать."""
    document_id = uuid4()

    progress_stub = UpdateAnalysisProgressStub(
        result=AnalysisProgressUpdateResult(
            changed=True,
            cancelled=False,
        ),
    )

    with build_client(
        progress_stub=progress_stub,
    ) as client:
        response = client.post(
            f"/internal/v1/analysis-progress/{document_id}",
            json={
                "stage": "extracting_sources",
            },
        )

    assert response.status_code == 200

    assert response.json() == {
        "status": "ok",
        "stage": "extracting_sources",
        "changed": True,
        "cancelled": False,
    }

    assert progress_stub.called_document_id == document_id
    assert progress_stub.called_stage is AnalysisProgressStage.EXTRACTING_SOURCES


def test_progress_checkpoint_returns_cancellation_signal() -> None:
    """Durable cancellation передаётся workflow успешным HTTP response."""
    document_id = uuid4()

    progress_stub = UpdateAnalysisProgressStub(
        result=AnalysisProgressUpdateResult(
            changed=False,
            cancelled=True,
        ),
    )

    with build_client(
        progress_stub=progress_stub,
    ) as client:
        response = client.post(
            f"/internal/v1/analysis-progress/{document_id}",
            json={
                "stage": "checking_requirements",
            },
        )

    assert response.status_code == 200

    assert response.json() == {
        "status": "ok",
        "stage": "checking_requirements",
        "changed": False,
        "cancelled": True,
    }


def test_progress_checkpoint_unknown_document_returns_404() -> None:
    """Неизвестный document_id сохраняет прежний 404 contract."""
    document_id = uuid4()

    progress_stub = UpdateAnalysisProgressStub(
        error=AnalysisProgressJobNotFoundError(
            f"Analysis job для document_id={document_id} не найден.",
        ),
    )

    with build_client(
        progress_stub=progress_stub,
    ) as client:
        response = client.post(
            f"/internal/v1/analysis-progress/{document_id}",
            json={
                "stage": "preparing_context",
            },
        )

    assert response.status_code == 404
    assert str(document_id) in response.json()["detail"]
