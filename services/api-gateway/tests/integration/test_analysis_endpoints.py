# services/api-gateway/tests/integration/test_analysis_endpoints.py

"""HTTP contract tests асинхронного analysis API."""

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pdrd_api_gateway.application.use_cases.check_readiness import (
    CheckReadiness,
)
from pdrd_api_gateway.core.container import ApplicationContainer
from pdrd_api_gateway.core.settings import (
    DatabaseSettings,
    Settings,
)
from pdrd_api_gateway.domain.analysis_job import AnalysisJob
from pdrd_api_gateway.main import create_app


class StaticReadiness:
    """Fake infrastructure readiness."""

    async def is_ready(self) -> bool:
        """Всегда сообщает готовность."""
        return True


class CreateAnalysisStub:
    """Fake CreateAnalysisJob."""

    def __init__(self) -> None:
        """Подготавливает stub."""
        self.document_id: UUID | None = None

    async def execute(
        self,
        *,
        document_id: UUID,
    ) -> AnalysisJob:
        """Создаёт domain job без database."""
        self.document_id = document_id

        return AnalysisJob.create(
            document_id=document_id,
        )


class GetAnalysisStub:
    """Fake GetAnalysisJob."""

    def __init__(
        self,
        job: AnalysisJob | None,
    ) -> None:
        """Сохраняет ожидаемый result."""
        self._job = job

    async def execute(
        self,
        *,
        job_id: UUID,
    ) -> AnalysisJob | None:
        """Возвращает настроенный result."""
        if self._job is not None and self._job.id == job_id:
            return self._job

        return None


async def noop_shutdown() -> None:
    """Имитирует освобождение resources."""


def build_client(
    *,
    create_stub: CreateAnalysisStub,
    get_stub: GetAnalysisStub,
) -> TestClient:
    """Создаёт HTTP client с fake application use cases."""
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
        create_analysis_job=create_stub,  # type: ignore[arg-type]
        get_analysis_job=get_stub,  # type: ignore[arg-type]
    )

    return TestClient(
        create_app(
            container=container,
        )
    )


def test_create_analysis_returns_202() -> None:
    """Проверяет asynchronous HTTP contract."""
    document_id = uuid4()

    create_stub = CreateAnalysisStub()

    with build_client(
        create_stub=create_stub,
        get_stub=GetAnalysisStub(None),
    ) as client:
        response = client.post(
            "/api/v1/analyses",
            json={
                "document_id": str(document_id),
            },
        )

    assert response.status_code == 202

    payload = response.json()

    assert payload["status"] == "pending"
    assert payload["status_url"] == (f"/api/v1/analyses/{payload['job_id']}")

    assert create_stub.document_id == document_id


def test_get_analysis_returns_job() -> None:
    """Проверяет получение состояния существующего job."""
    job = AnalysisJob.create(
        document_id=uuid4(),
    )

    with build_client(
        create_stub=CreateAnalysisStub(),
        get_stub=GetAnalysisStub(job),
    ) as client:
        response = client.get(
            f"/api/v1/analyses/{job.id}",
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["job_id"] == str(job.id)
    assert payload["document_id"] == str(
        job.document_id,
    )
    assert payload["status"] == "pending"


def test_get_unknown_analysis_returns_404() -> None:
    """Проверяет HTTP 404 для неизвестного job."""
    with build_client(
        create_stub=CreateAnalysisStub(),
        get_stub=GetAnalysisStub(None),
    ) as client:
        response = client.get(
            f"/api/v1/analyses/{uuid4()}",
        )

    assert response.status_code == 404


def test_create_analysis_rejects_invalid_document_id() -> None:
    """Проверяет validation UUID."""
    with build_client(
        create_stub=CreateAnalysisStub(),
        get_stub=GetAnalysisStub(None),
    ) as client:
        response = client.post(
            "/api/v1/analyses",
            json={
                "document_id": "not-a-uuid",
            },
        )

    assert response.status_code == 422
