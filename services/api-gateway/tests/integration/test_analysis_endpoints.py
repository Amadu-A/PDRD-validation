# services/api-gateway/tests/integration/test_analysis_endpoints.py

"""HTTP contract tests асинхронного analysis API."""

from collections.abc import (
    Awaitable,
    Callable,
)
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
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

    async def is_ready(self) -> bool:
        """Всегда сообщает готовность."""
        return True


class SubmitAnalysisStub:
    """Fake SubmitAnalysis."""

    def __init__(self) -> None:
        """Подготавливает stub."""
        self.pdf_content: bytes | None = None
        self.pdf_file_name: str | None = None

        self.cad_content: bytes | None = None
        self.cad_file_name: str | None = None

        self.pages: str | None = None

        self.use_explanatory_note = False
        self.note_start_page: str | int | None = None
        self.note_end_page: str | int | None = None

        self.document_id: UUID | None = None

    async def execute(
        self,
        *,
        pdf_content: bytes | None,
        pdf_file_name: str | None,
        cad_content: bytes | None,
        cad_file_name: str | None,
        pages: str | None,
        use_explanatory_note: bool = False,
        note_start_page: str | int | None = None,
        note_end_page: str | int | None = None,
    ) -> AnalysisJob:
        """Создаёт fake job и сохраняет аргументы."""
        self.pdf_content = pdf_content
        self.pdf_file_name = pdf_file_name

        self.cad_content = cad_content
        self.cad_file_name = cad_file_name

        self.pages = pages

        self.use_explanatory_note = use_explanatory_note

        self.note_start_page = note_start_page
        self.note_end_page = note_end_page

        self.document_id = uuid4()

        return AnalysisJob.create(
            document_id=self.document_id,
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
    submit_stub: SubmitAnalysisStub,
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
        get_analysis_job=get_stub,  # type: ignore[arg-type]
        submit_analysis=submit_stub,  # type: ignore[arg-type]
    )

    return TestClient(
        create_app(
            container=container,
        )
    )


def test_create_pdf_analysis_returns_202() -> None:
    """Проверяет asynchronous PDF upload contract."""
    submit_stub = SubmitAnalysisStub()

    with build_client(
        submit_stub=submit_stub,
        get_stub=GetAnalysisStub(None),
    ) as client:
        response = client.post(
            "/api/v1/analyses",
            files={
                "pdf": (
                    "drawing.pdf",
                    b"pdf-content",
                    "application/pdf",
                ),
            },
            data={
                "pages": "1,3",
            },
        )

    assert response.status_code == 202

    payload = response.json()

    assert payload["status"] == "pending"

    assert payload["document_id"] == str(
        submit_stub.document_id,
    )

    assert payload["status_url"] == (f"/api/v1/analyses/{payload['job_id']}")

    assert submit_stub.pdf_content == b"pdf-content"

    assert submit_stub.pdf_file_name == "drawing.pdf"

    assert submit_stub.cad_content is None
    assert submit_stub.pages == "1,3"

    assert submit_stub.use_explanatory_note is False


def test_create_pdf_analysis_with_note_returns_202() -> None:
    """Проверяет HTTP transport параметров ПЗ."""
    submit_stub = SubmitAnalysisStub()

    with build_client(
        submit_stub=submit_stub,
        get_stub=GetAnalysisStub(None),
    ) as client:
        response = client.post(
            "/api/v1/analyses",
            files={
                "pdf": (
                    "drawing.pdf",
                    b"pdf-content",
                    "application/pdf",
                ),
            },
            data={
                "pages": "10",
                "use_explanatory_note": "true",
                "note_start_page": "2",
                "note_end_page": "8",
            },
        )

    assert response.status_code == 202

    assert submit_stub.use_explanatory_note is True

    assert submit_stub.note_start_page == "2"
    assert submit_stub.note_end_page == "8"


def test_create_pdf_cad_analysis_returns_202() -> None:
    """Проверяет combined multipart contract."""
    submit_stub = SubmitAnalysisStub()

    with build_client(
        submit_stub=submit_stub,
        get_stub=GetAnalysisStub(None),
    ) as client:
        response = client.post(
            "/api/v1/analyses",
            files={
                "pdf": (
                    "drawing.pdf",
                    b"pdf-content",
                    "application/pdf",
                ),
                "cad": (
                    "drawing.dxf",
                    b"cad-content",
                    "application/dxf",
                ),
            },
            data={
                "pages": "7",
            },
        )

    assert response.status_code == 202

    assert submit_stub.pdf_file_name == "drawing.pdf"

    assert submit_stub.cad_file_name == "drawing.dxf"

    assert submit_stub.pages == "7"


def test_get_analysis_returns_job() -> None:
    """Проверяет получение состояния существующего job."""
    job = AnalysisJob.create(
        document_id=uuid4(),
    )

    with build_client(
        submit_stub=SubmitAnalysisStub(),
        get_stub=GetAnalysisStub(job),
    ) as client:
        response = client.get(
            f"/api/v1/analyses/{job.id}",
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["job_id"] == str(
        job.id,
    )

    assert payload["document_id"] == str(
        job.document_id,
    )

    assert payload["status"] == "pending"


def test_get_unknown_analysis_returns_404() -> None:
    """Проверяет HTTP 404 для неизвестного job."""
    with build_client(
        submit_stub=SubmitAnalysisStub(),
        get_stub=GetAnalysisStub(None),
    ) as client:
        response = client.get(
            f"/api/v1/analyses/{uuid4()}",
        )

    assert response.status_code == 404
